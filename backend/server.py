"""
Transit E1 - Bus Booking System Backend
FastAPI + MongoDB. API-first so future mobile (React Native) uses same endpoints.
"""
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError
import os
import logging
import uuid
import asyncio
import hashlib
import re
import secrets
import bcrypt
import jwt
import pyotp
from functools import lru_cache
from cachetools import TTLCache
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import List, Optional, Literal, Dict
from datetime import datetime, timezone, timedelta
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionResponse,
    CheckoutStatusResponse,
    CheckoutSessionRequest,
)
import stripe as stripe_sdk
import httpx
from email_service import (
    send_booking_confirmation,
    send_password_reset,
    send_admin_invite,
    send_feedback_confirmation,
    send_feedback_admin_notification,
)

# ---------- Setup ----------
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "168"))
STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
if not BOOTSTRAP_ADMIN_PASSWORD:
    raise RuntimeError(
        "BOOTSTRAP_ADMIN_PASSWORD env var is required. Set a strong password in backend/.env"
    )

app = FastAPI(title="Transit E1 - Bus Booking API")
api = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("transit")

# In-process TTL caches (single-instance VPS friendly).
# When we move to multi-worker, swap the backing store for Redis using the same
# get/set pattern.
_terminals_cache: TTLCache = TTLCache(maxsize=8, ttl=300)   # 5 min
_popular_cache: TTLCache = TTLCache(maxsize=8, ttl=30)      # 30 sec
_settings_cache: TTLCache = TTLCache(maxsize=2, ttl=60)     # 1 min


# ---------- Helpers ----------
def utcnow():
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _fingerprint(token: str) -> str:
    """Fast deterministic SHA256 hex digest for indexed token lookup.

    We still bcrypt-verify the candidate for constant-time defence in depth, but the
    fingerprint lets us O(1)-locate the row instead of scanning + bcrypt-checking
    every active token (which would be O(N × bcrypt_cost) and slow at scale).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def issue_jwt(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


async def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Optional[dict]:
    """Returns user dict or None (guest). Raises if token is malformed."""
    if not creds or not creds.credentials:
        return None
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0, "totp_secret": 0})
    return user


async def require_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
    user = await current_user(creds)
    if not user:
        raise HTTPException(401, "Authentication required")
    if user.get("is_active") is False:
        raise HTTPException(403, "This account has been deactivated")
    return user


def _layout_for_bus_type(bus_type: str) -> str:
    """Map a bus class name to a seat-column layout. VIP = luxury 2+1, everything else = 2+2."""
    bt = (bus_type or "").strip().lower()
    if "vip" in bt:
        return "2+1"
    return "2+2"


async def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


async def require_super_admin(user: dict = Depends(require_admin)) -> dict:
    """Super-admins manage other admins. Regular admins cannot."""
    if user.get("role") != "super_admin":
        raise HTTPException(403, "Super-admin access required")
    return user


async def log_audit(
    user: dict,
    action: str,
    resource: str,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    request: Optional[Request] = None,
):
    """Record an admin action to the audit_logs collection."""
    ip = None
    if request is not None:
        # Respect X-Forwarded-For if present (behind proxy/ingress)
        fwd = request.headers.get("x-forwarded-for")
        ip = (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else None)
    try:
        await db.audit_logs.insert_one({
            "id": new_id(),
            "actor_id": user.get("id"),
            "actor_email": user.get("email"),
            "action": action,           # e.g. "create", "update", "delete", "toggle"
            "resource": resource,       # e.g. "terminal", "schedule", "promo_code"
            "resource_id": resource_id,
            "details": details or {},
            "ip": ip,
            "created_at": utcnow().isoformat(),
        })
    except Exception as e:
        # Audit logging must never break the main request
        logger.warning("audit log failed: %s", e)


# ---------- Login rate limiting (brute-force protection) ----------
LOGIN_MAX_ATTEMPTS_PER_IDENTIFIER = 5   # same IP + email within window
LOGIN_MAX_ATTEMPTS_PER_IP = 20          # same IP across all emails within window
LOGIN_WINDOW_SECONDS = 15 * 60          # 15 minutes


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_login_rate_limit(ip: str, email: str):
    """Raise 429 if this IP+email or this IP alone has exceeded failed-login thresholds."""
    window_start = utcnow() - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    identifier = f"{ip}:{email.lower()}"

    id_attempts = await db.login_attempts.count_documents({
        "identifier": identifier,
        "failed_at": {"$gte": window_start},
    })
    if id_attempts >= LOGIN_MAX_ATTEMPTS_PER_IDENTIFIER:
        raise HTTPException(
            429,
            detail=f"Too many failed login attempts. Please try again in {LOGIN_WINDOW_SECONDS // 60} minutes.",
            headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)},
        )

    ip_attempts = await db.login_attempts.count_documents({
        "ip": ip,
        "failed_at": {"$gte": window_start},
    })
    if ip_attempts >= LOGIN_MAX_ATTEMPTS_PER_IP:
        raise HTTPException(
            429,
            detail="Too many failed login attempts from this network. Please try again later.",
            headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)},
        )


async def _record_login_failure(ip: str, email: str):
    try:
        await db.login_attempts.insert_one({
            "identifier": f"{ip}:{email.lower()}",
            "ip": ip,
            "email": email.lower(),
            "failed_at": utcnow(),
        })
    except Exception as e:
        logger.warning("record_login_failure failed: %s", e)


async def _clear_login_attempts(ip: str, email: str):
    try:
        await db.login_attempts.delete_many({"identifier": f"{ip}:{email.lower()}"})
    except Exception as e:
        logger.warning("clear_login_attempts failed: %s", e)


# Generic throttle for other auth actions (register, 2FA). Uses `auth_throttle`
# collection with per-document TTL via `expires_at`, so each scope can have its
# own window without clashing with the login rate-limit collection.
async def _check_auth_throttle(ip: str, scope: str, max_attempts: int, window_seconds: int,
                               label: str, identifier: Optional[str] = None):
    query: dict = {
        "ip": ip,
        "scope": scope,
        "failed_at": {"$gte": utcnow() - timedelta(seconds=window_seconds)},
    }
    if identifier:
        query["identifier"] = identifier
    count = await db.auth_throttle.count_documents(query)
    if count >= max_attempts:
        raise HTTPException(
            429,
            detail=f"Too many {label} attempts. Please try again in {max(1, window_seconds // 60)} minutes.",
            headers={"Retry-After": str(window_seconds)},
        )


async def _record_auth_failure(ip: str, scope: str, window_seconds: int,
                               identifier: Optional[str] = None):
    try:
        now = utcnow()
        await db.auth_throttle.insert_one({
            "ip": ip,
            "scope": scope,
            "identifier": identifier,
            "failed_at": now,
            "expires_at": now + timedelta(seconds=window_seconds),
        })
    except Exception as e:
        logger.warning("record_auth_failure failed: %s", e)


async def _clear_auth_attempts(ip: str, scope: str, identifier: Optional[str] = None):
    try:
        q: dict = {"ip": ip, "scope": scope}
        if identifier:
            q["identifier"] = identifier
        await db.auth_throttle.delete_many(q)
    except Exception as e:
        logger.warning("clear_auth_attempts failed: %s", e)


# ---------- Password strength ----------
# Compact list of passwords banned outright — covers SecLists top-100 + common
# local variants. Comparison is case-insensitive.
COMMON_PASSWORDS = {
    "123456", "123456789", "12345678", "12345", "1234567", "1234567890",
    "password", "password1", "password123", "qwerty", "qwerty123", "qwertyuiop",
    "abc123", "111111", "123123", "000000", "iloveyou", "admin", "admin123",
    "administrator", "letmein", "welcome", "welcome1", "monkey", "dragon",
    "master", "sunshine", "princess", "football", "baseball", "superman",
    "batman", "trustno1", "starwars", "passw0rd", "1q2w3e4r", "1qaz2wsx",
    "zaq12wsx", "qazwsx", "asdfgh", "asdfghjkl", "qwerty1", "qwertyu",
    "pokemon", "hello", "hello123", "hello1", "charlie", "whatever",
    "shadow", "ashley", "michael", "jennifer", "thomas", "jordan", "jessica",
    "robert", "daniel", "andrew", "joshua", "matthew", "nicole", "amanda",
    "taylor", "hunter", "buster", "soccer", "hockey", "killer", "george",
    "sexy", "andrea", "michelle", "love", "login", "test", "test123",
    "guest", "user", "root", "toor", "changeme", "qwer1234", "qwer123",
    "p@ssw0rd", "p@ssword", "pa55word", "pass123", "pass1234", "pass12345",
    "starqistna", "starqistna123", "bus123", "ticket123",
    "malaysia", "malaysia123", "kuala", "singapore",
}


def _validate_password_strength(password: str) -> None:
    """Raise HTTPException(400) with a user-friendly message if the password is
    too weak. Enforced rules: min 8 chars, 1 upper, 1 lower, 1 digit, not on
    the common-password blocklist."""
    pw = password or ""
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters long.")
    if not any(c.isupper() for c in pw):
        raise HTTPException(400, "Password must contain at least one uppercase letter.")
    if not any(c.islower() for c in pw):
        raise HTTPException(400, "Password must contain at least one lowercase letter.")
    if not any(c.isdigit() for c in pw):
        raise HTTPException(400, "Password must contain at least one number.")
    if pw.lower() in COMMON_PASSWORDS:
        raise HTTPException(400, "This password is too common. Please pick something unique.")


# ---------- Models ----------
class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    phone: Optional[str] = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class TwoFASetupBody(BaseModel):
    password: str


class TwoFAEnableBody(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TwoFADisableBody(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=6)


class TwoFAVerifyBody(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=6)


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=8)


class AdminInviteBody(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "super_admin"] = "admin"


class AdminAcceptInviteBody(BaseModel):
    token: str = Field(min_length=16)
    password: str = Field(min_length=8)


class AdminRoleUpdateBody(BaseModel):
    role: Literal["admin", "super_admin"]


class AdminActiveUpdateBody(BaseModel):
    is_active: bool


class FeedbackBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    email: EmailStr
    category: Literal["general", "booking_issue", "complaint", "suggestion", "praise"] = "general"
    booking_reference: Optional[str] = Field(default=None, max_length=40)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=10, max_length=4000)


class FeedbackStatusBody(BaseModel):
    status: Literal["new", "in_progress", "resolved"]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class TerminalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    city: str
    name: str
    code: str
    state: Optional[str] = None


class PassengerInput(BaseModel):
    name: str
    category: Literal["adult", "child"]
    ic_or_passport: Optional[str] = None


class SeatLockBody(BaseModel):
    schedule_id: str
    seat_numbers: List[str]


class CreateBookingBody(BaseModel):
    schedule_id: str
    seat_assignments: List[dict]  # [{seat_number, passenger_index}]
    passengers: List[PassengerInput]
    contact_email: EmailStr
    contact_phone: str
    promo_code: Optional[str] = None
    # When booking a specific segment of a multi-stop route, these override the
    # schedule's own from/to and switch fare pricing to the route's pairing entry.
    # If omitted, the schedule's origin→final-destination is assumed (legacy).
    pickup_terminal_id: Optional[str] = None
    dropoff_terminal_id: Optional[str] = None


class CheckoutBody(BaseModel):
    booking_id: str
    origin_url: str
    gateway: str = "card"  # stripe payment_method_types: card | grabpay | fpx


class PromoValidateBody(BaseModel):
    code: str
    schedule_id: str
    passenger_count: int = Field(ge=1)
    adults: int = Field(ge=0)
    children: int = Field(ge=0)


class PromoCreateBody(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    type: Literal["percent", "flat"]
    value: float = Field(gt=0)
    currency: str = "myr"
    max_uses: Optional[int] = None
    valid_until: Optional[str] = None  # ISO date
    active: bool = True
    description: Optional[str] = None


class BoardingValidateBody(BaseModel):
    reference: str
    gate: Optional[str] = None
    mark_boarded: bool = True


# ---------- Search & Terminals ----------
@api.get("/terminals")
async def list_terminals(q: Optional[str] = None):
    # Cache the unfiltered list (hot path on every search page).
    cache_key = "all"
    query = {}
    if q:
        # Escape user input to prevent ReDoS / regex injection. Cap length to
        # protect against pathological patterns and DB load.
        safe_q = re.escape(q.strip()[:64])
        query = {
            "$or": [
                {"city": {"$regex": safe_q, "$options": "i"}},
                {"name": {"$regex": safe_q, "$options": "i"}},
                {"code": {"$regex": safe_q, "$options": "i"}},
            ]
        }
        cache_key = None  # don't cache filtered results

    if cache_key and cache_key in _terminals_cache:
        return _terminals_cache[cache_key]

    terminals = await db.terminals.find(query, {"_id": 0}).sort("city", 1).to_list(500)
    # Group by city for UI
    by_city: dict = {}
    for t in terminals:
        by_city.setdefault(t["city"], []).append(t)
    grouped = [{"city": city, "terminals": sorted(items, key=lambda x: x["name"])} for city, items in sorted(by_city.items())]
    payload = {"grouped": grouped, "all": terminals}
    if cache_key:
        _terminals_cache[cache_key] = payload
    return payload


@api.get("/search")
async def search_schedules(
    date: str,  # YYYY-MM-DD
    from_terminal_id: Optional[str] = None,
    to_terminal_id: Optional[str] = None,
    from_city: Optional[str] = None,
    to_city: Optional[str] = None,
):
    """Search schedules for a date.

    Two modes:
      * Stop-level: caller passes `from_terminal_id` + `to_terminal_id`
        (existing behaviour — one specific pickup → one specific drop-off).
      * City-level: caller passes `from_city` and/or `to_city` — we expand
        the query to every terminal in that city. Used when the customer
        picks "Any stop in Kuala Lumpur" on the homepage.

    You can mix modes (e.g. city → specific terminal). At least one side of
    each pair must resolve to something or we return 400.
    """
    from_ids: List[str] = []
    to_ids: List[str] = []

    if from_terminal_id:
        from_ids = [from_terminal_id]
    elif from_city:
        from_ids = [t["id"] async for t in db.terminals.find(
            {"city": from_city, "is_pickup": {"$ne": False}}, {"_id": 0, "id": 1}
        )]
        # Backward-compat: terminals seeded before the is_pickup field default true.
        if not from_ids:
            from_ids = [t["id"] async for t in db.terminals.find({"city": from_city}, {"_id": 0, "id": 1})]
    if to_terminal_id:
        to_ids = [to_terminal_id]
    elif to_city:
        to_ids = [t["id"] async for t in db.terminals.find(
            {"city": to_city, "is_dropoff": {"$ne": False}}, {"_id": 0, "id": 1}
        )]
        if not to_ids:
            to_ids = [t["id"] async for t in db.terminals.find({"city": to_city}, {"_id": 0, "id": 1})]

    if not from_ids or not to_ids:
        raise HTTPException(400, "Provide either from_terminal_id or from_city (and same for `to`).")

    # Find schedules matching date + terminal set.
    if len(from_ids) == 1 and len(to_ids) == 1:
        query = {
            "from_terminal_id": from_ids[0],
            "to_terminal_id": to_ids[0],
            "departure_date": date,
        }
    else:
        query = {
            "from_terminal_id": {"$in": from_ids},
            "to_terminal_id": {"$in": to_ids},
            "departure_date": date,
        }

    # If searching for "today" in local Malaysia/Singapore time (UTC+8 — same offset for both),
    # hide buses whose departure time has already passed so users don't book a departed trip.
    local_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(local_tz)
    today_local_iso = now_local.date().isoformat()
    if date == today_local_iso:
        query["departure_time"] = {"$gte": now_local.strftime("%H:%M")}

    # Exclude schedules whose bulk-creation `end_date` (last trip in the series)
    # has already passed. Rows without the field pass through unchanged.
    query["$or"] = [
        {"end_date": {"$exists": False}},
        {"end_date": None},
        {"end_date": ""},
        {"end_date": {"$gte": date}},
    ]

    schedules = await db.schedules.find(query, {"_id": 0}).sort("departure_time", 1).to_list(200)

    # -------------------------------------------------------------------------
    # Route-segment matching:
    #
    # A route-linked schedule stores from = route.origin, to = route.destination
    # (the whole physical bus). But customers might want any (pickup, dropoff)
    # pair the route's pairings advertise — e.g. "Melaka → JB" on a KL→SG bus.
    #
    # For each requested (pickup_id, dropoff_id) combination we also find route-
    # linked schedules whose route offers that pairing, then splice the pairing's
    # fare + specific segment into the returned row.
    # -------------------------------------------------------------------------
    matching_routes = await db.routes.find(
        {"is_active": {"$ne": False},
         "pairings.pickup_id": {"$in": from_ids},
         "pairings.dropoff_id": {"$in": to_ids}},
        {"_id": 0},
    ).to_list(200)
    # Build (pickup_id, dropoff_id) → pairing map per route so we know which segment
    # to attach for the specific customer query.
    segment_by_route: Dict[str, dict] = {}
    for r in matching_routes:
        for p in r.get("pairings", []):
            if p.get("pickup_id") in from_ids and p.get("dropoff_id") in to_ids:
                # Pick the first matching pairing per route (should be unique per pair).
                segment_by_route.setdefault(r["id"], {"route": r, "pairing": p})
    if segment_by_route:
        # Fetch schedules linked to any of these routes on this date.
        route_ids = list(segment_by_route.keys())
        seg_query: dict = {
            "route_id": {"$in": route_ids},
            "departure_date": date,
        }
        if "departure_time" in query:  # today-hides-past filter carries over
            seg_query["departure_time"] = query["departure_time"]
        seg_query["$or"] = query["$or"]  # end_date guard
        existing_ids = {s["id"] for s in schedules}
        seg_schedules = await db.schedules.find(seg_query, {"_id": 0}).to_list(200)
        for s in seg_schedules:
            if s["id"] in existing_ids:
                continue  # already returned via direct-match branch
            info = segment_by_route.get(s.get("route_id"))
            if not info:
                continue
            pairing = info["pairing"]
            # Overlay the sold segment on the row so the frontend renders correctly.
            s["from_terminal_id"] = pairing["pickup_id"]
            s["to_terminal_id"] = pairing["dropoff_id"]
            s["adult_fare"] = float(pairing.get("adult_fare", s.get("adult_fare", 0)))
            s["child_fare"] = float(pairing.get("child_fare", s.get("child_fare", 0)))
            s["currency"] = (pairing.get("currency") or s.get("currency") or "myr").lower()
            s["is_route_segment"] = True
            schedules.append(s)
        # Keep the result sorted by departure_time for a natural timeline view.
        schedules.sort(key=lambda x: x.get("departure_time", ""))

    # Enrich each schedule with the SPECIFIC pickup + drop-off terminal it uses.
    # Critical for city-level searches ("Any stop in KL → Any stop in SG") where
    # the user needs to know exactly which terminal to show up at.
    if schedules:
        terminal_ids = list({s["from_terminal_id"] for s in schedules} |
                            {s["to_terminal_id"] for s in schedules})
        term_docs = await db.terminals.find(
            {"id": {"$in": terminal_ids}},
            {"_id": 0, "id": 1, "code": 1, "name": 1, "city": 1, "landmark_address": 1},
        ).to_list(len(terminal_ids))
        term_by_id = {t["id"]: t for t in term_docs}
        for s in schedules:
            s["from_terminal"] = term_by_id.get(s["from_terminal_id"])
            s["to_terminal"] = term_by_id.get(s["to_terminal_id"])

    # Enrich with terminal names — for single-stop searches show the picked stop,
    # for city-level searches show the city as the label + count of options.
    if from_terminal_id:
        from_meta = await db.terminals.find_one({"id": from_terminal_id}, {"_id": 0})
    else:
        from_meta = {"city": from_city, "name": f"Any stop · {from_city}", "code": None,
                     "is_city": True, "stop_count": len(from_ids)}
    if to_terminal_id:
        to_meta = await db.terminals.find_one({"id": to_terminal_id}, {"_id": 0})
    else:
        to_meta = {"city": to_city, "name": f"Any stop · {to_city}", "code": None,
                   "is_city": True, "stop_count": len(to_ids)}

    # Single aggregation for booked-seat counts across ALL schedules in this result.
    # Replaces an N+1 pattern (1 count_documents per schedule) with one round-trip.
    if schedules:
        sched_ids = [s["id"] for s in schedules]
        pipeline = [
            {"$match": {"schedule_id": {"$in": sched_ids}, "status": {"$in": ["locked", "booked"]}}},
            {"$group": {"_id": "$schedule_id", "count": {"$sum": 1}}},
        ]
        counts = {row["_id"]: row["count"] async for row in db.seat_locks.aggregate(pipeline)}
        for s in schedules:
            s["seats_available"] = s["total_seats"] - counts.get(s["id"], 0)

    return {
        "from": from_meta,
        "to": to_meta,
        "date": date,
        "schedules": schedules,
    }


# ---------- Popular Right Now ----------
# Hand-picked time-aware suggestions: the soonest upcoming bus on each popular pair.
POPULAR_PAIRS = [
    ("Kuala Lumpur", "Melaka"),
    ("Kuala Lumpur", "Johor Bahru"),
    ("Kuala Lumpur", "Penang"),
    ("Kuala Lumpur", "Singapore"),
    ("Penang", "Johor Bahru"),
    ("Singapore", "Kuala Lumpur"),
    ("Johor Bahru", "Kuala Lumpur"),
    ("Penang", "Kuala Lumpur"),
]


@api.get("/popular/now")
async def popular_now(limit: int = 6):
    """Return time-aware 'next bus' suggestions for popular city pairs.

    For each pair we pick the soonest upcoming schedule (today or next few days),
    enrich with terminals + seats-available + pricing. Uses local Malaysia/Singapore
    time (UTC+8) because schedule `departure_time` is stored as local HH:MM.

    Cached for 30 seconds — countdowns are computed client-side anyway, and seat
    counts on this cosmetic widget can lag a few seconds without harm.
    """
    limit = max(1, min(limit, 12))
    cache_key = f"limit:{limit}"
    if cache_key in _popular_cache:
        return _popular_cache[cache_key]

    local_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(local_tz)
    now_utc = datetime.now(timezone.utc)
    today_iso = now_local.date().isoformat()
    current_hhmm = now_local.strftime("%H:%M")

    # Pre-load terminals indexed by city -> first terminal
    all_terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    city_to_term: dict = {}
    for t in all_terms:
        city_to_term.setdefault(t["city"], t)

    suggestions = []
    for from_city, to_city in POPULAR_PAIRS:
        f = city_to_term.get(from_city)
        t = city_to_term.get(to_city)
        if not f or not t:
            continue

        # Find nearest upcoming schedule (today after now, or any day in next 7 days)
        sched = await db.schedules.find_one(
            {
                "from_terminal_id": f["id"],
                "to_terminal_id": t["id"],
                "departure_date": today_iso,
                "departure_time": {"$gte": current_hhmm},
            },
            {"_id": 0},
            sort=[("departure_time", 1)],
        )
        if not sched:
            # Fall back to the next 7 days
            sched = await db.schedules.find_one(
                {
                    "from_terminal_id": f["id"],
                    "to_terminal_id": t["id"],
                    "departure_date": {"$gt": today_iso},
                },
                {"_id": 0},
                sort=[("departure_date", 1), ("departure_time", 1)],
            )
        if not sched:
            continue

        # Seats remaining
        booked_count = await db.seat_locks.count_documents(
            {"schedule_id": sched["id"], "status": {"$in": ["locked", "booked"]}}
        )
        seats_available = sched["total_seats"] - booked_count

        # How many minutes until departure (positive only). Departure stored as local MY HH:MM.
        try:
            dep_dt = datetime.fromisoformat(
                f"{sched['departure_date']}T{sched['departure_time']}:00+08:00"
            )
            mins_until = max(0, int((dep_dt - now_local).total_seconds() // 60))
        except Exception:
            mins_until = None

        suggestions.append(
            {
                "from_city": from_city,
                "to_city": to_city,
                "from_terminal": f,
                "to_terminal": t,
                "schedule_id": sched["id"],
                "departure_date": sched["departure_date"],
                "departure_time": sched["departure_time"],
                "arrival_time": sched.get("arrival_time"),
                "is_today": sched["departure_date"] == today_iso,
                "minutes_until_departure": mins_until,
                "fare": sched.get("adult_fare"),
                "currency": sched.get("currency", "myr"),
                "seats_available": seats_available,
                "bus_type": sched.get("bus_type"),
                "operator": sched.get("bus_operator", "Qistna Express"),
            }
        )

    # Sort: today's first, then by minutes_until_departure asc
    suggestions.sort(
        key=lambda x: (
            0 if x["is_today"] else 1,
            x.get("minutes_until_departure") if x.get("minutes_until_departure") is not None else 10**9,
        )
    )
    payload = {"generated_at": now_utc.isoformat(), "items": suggestions[:limit]}
    _popular_cache[cache_key] = payload
    return payload


@api.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str):
    sched = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")
    from_term = await db.terminals.find_one({"id": sched["from_terminal_id"]}, {"_id": 0})
    to_term = await db.terminals.find_one({"id": sched["to_terminal_id"]}, {"_id": 0})

    # seat states
    locks = await db.seat_locks.find(
        {"schedule_id": schedule_id, "status": {"$in": ["locked", "booked"]}},
        {"_id": 0},
    ).to_list(500)
    booked_seats = {lk["seat_number"]: lk["status"] for lk in locks}

    # Derive seat layout. Supports 2+2 (A,B | C,D = 4/row) and 2+1 (A,B | C = 3/row).
    # `layout_config` persisted on schedule overrides `bus_type` mapping.
    layout_config = sched.get("layout_config") or _layout_for_bus_type(sched.get("bus_type", "Standard"))
    cols_left, cols_right = (("A", "B"), ("C", "D")) if layout_config == "2+2" else (("A", "B"), ("C",))
    per_row = len(cols_left) + len(cols_right)

    total_seats = int(sched.get("total_seats") or (sched.get("rows", 10) * 4))
    # Last row may be partial when total_seats is not a clean multiple of per_row.
    full_rows = total_seats // per_row
    remainder = total_seats - full_rows * per_row

    layout = []
    seats_emitted = 0

    def seat_cell(sn: str):
        return {"seat_number": sn, "status": booked_seats.get(sn, "available")}

    for r in range(1, full_rows + 1):
        row_seats = []
        for col in cols_left:
            row_seats.append(seat_cell(f"{r}{col}"))
            seats_emitted += 1
        row_seats.append({"seat_number": None, "aisle": True})
        for col in cols_right:
            row_seats.append(seat_cell(f"{r}{col}"))
            seats_emitted += 1
        layout.append(row_seats)

    # Partial last row (e.g. 27-seater 2+1 → 9 full rows; or 2+2 with 39 seats → last row 3 seats)
    if remainder > 0:
        r = full_rows + 1
        row_seats = []
        remaining = remainder
        for col in cols_left:
            if remaining <= 0:
                row_seats.append({"seat_number": None, "empty": True})
                continue
            row_seats.append(seat_cell(f"{r}{col}"))
            remaining -= 1
            seats_emitted += 1
        row_seats.append({"seat_number": None, "aisle": True})
        for col in cols_right:
            if remaining <= 0:
                row_seats.append({"seat_number": None, "empty": True})
                continue
            row_seats.append(seat_cell(f"{r}{col}"))
            remaining -= 1
            seats_emitted += 1
        layout.append(row_seats)

    return {
        "schedule": sched,
        "from": from_term,
        "to": to_term,
        "layout": layout,
        "layout_config": layout_config,
        "seats_per_row": per_row,
    }


# ---------- Auth ----------
@api.post("/auth/register", response_model=TokenResponse)
async def register(body: RegisterBody, request: Request):
    ip = _client_ip(request)
    # Throttle signup spam: 10 registrations per hour per IP.
    await _check_auth_throttle(ip, "register", max_attempts=10, window_seconds=3600,
                                label="registration")
    # Record the attempt before we know success; we do NOT clear on success so honest
    # users on shared IPs (cafe/office) still have a generous quota.
    await _record_auth_failure(ip, "register", window_seconds=3600)

    _validate_password_strength(body.password)

    existing = await db.users.find_one({"email": body.email.lower()})
    if existing:
        raise HTTPException(400, "Email already registered")
    user_id = new_id()
    doc = {
        "id": user_id,
        "email": body.email.lower(),
        "full_name": body.full_name,
        "phone": body.phone,
        "password_hash": hash_password(body.password),
        "is_admin": False,
        "created_at": utcnow().isoformat(),
    }
    await db.users.insert_one(doc)
    user_public = {k: v for k, v in doc.items() if k not in ("password_hash", "_id", "totp_secret")}
    user_public["has_password"] = bool(doc.get("password_hash"))
    return TokenResponse(access_token=issue_jwt(user_id, doc["email"]), user=user_public)

async def _record_login(user: dict, ip: str) -> dict:
    """Rotate last-login fields: previous_login_* gets the prior values, and
    last_login_* is set to now/this-IP. Returns the updated user dict so the
    caller can include it in the response.

    Mirrors the bank-style "last login was on X from Y" UI cue — comparing the
    previous value lets users spot unfamiliar sessions immediately.
    """
    now_iso = utcnow().isoformat()
    prev_at = user.get("last_login_at")
    prev_ip = user.get("last_login_ip")
    update = {"last_login_at": now_iso, "last_login_ip": ip}
    if prev_at:
        update["previous_login_at"] = prev_at
        update["previous_login_ip"] = prev_ip
    await db.users.update_one({"id": user["id"]}, {"$set": update})
    user.update(update)
    return user




@api.post("/auth/login")
async def login(body: LoginBody, request: Request):
    ip = _client_ip(request)
    email = body.email.lower()
    await _check_login_rate_limit(ip, email)

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await _record_login_failure(ip, email)
        raise HTTPException(401, "Invalid email or password")

    # Password OK — clear password-attempt counters. The 2FA step has its own
    # protection via the 5-minute challenge expiry.
    await _clear_login_attempts(ip, email)

    if user.get("totp_enabled"):
        # Don't record the login yet — only after 2FA verifies.
        challenge = jwt.encode(
            {
                "sub": user["id"],
                "email": user["email"],
                "scope": "2fa_challenge",
                "exp": utcnow() + timedelta(minutes=5),
                "iat": utcnow(),
            },
            JWT_SECRET,
            algorithm=JWT_ALG,
        )
        return {"requires_2fa": True, "challenge_token": challenge}

    user = await _record_login(user, ip)
    user_public = {k: v for k, v in user.items() if k not in ("password_hash", "_id", "totp_secret")}
    user_public["has_password"] = bool(user.get("password_hash"))
    return TokenResponse(access_token=issue_jwt(user["id"], user["email"]), user=user_public)


@api.post("/auth/2fa/verify")
async def verify_2fa(body: TwoFAVerifyBody, request: Request):
    ip = _client_ip(request)
    try:
        payload = jwt.decode(body.challenge_token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Challenge expired — log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid challenge token")
    if payload.get("scope") != "2fa_challenge":
        raise HTTPException(401, "Invalid challenge scope")

    # Throttle 2FA guessing: 5 attempts per 15 min per IP + user (sub).
    throttle_id = f"{ip}:{payload['sub']}"
    await _check_auth_throttle(ip, "2fa", max_attempts=5, window_seconds=15 * 60,
                                label="2FA", identifier=throttle_id)

    user = await db.users.find_one({"id": payload["sub"]})
    if not user or not user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(400, "2FA is not enabled for this account")

    totp = pyotp.TOTP(user["totp_secret"])
    if not totp.verify(body.code, valid_window=1):
        await _record_auth_failure(ip, "2fa", window_seconds=15 * 60, identifier=throttle_id)
        raise HTTPException(401, "Invalid 2FA code")

    # Success — clear the counter so the user can log in again later without punishment.
    await _clear_auth_attempts(ip, "2fa", identifier=throttle_id)
    user = await _record_login(user, ip)
    user_public = {k: v for k, v in user.items() if k not in ("password_hash", "_id", "totp_secret")}
    user_public["has_password"] = bool(user.get("password_hash"))
    return TokenResponse(access_token=issue_jwt(user["id"], user["email"]), user=user_public)


# ---------- Forgot / Reset password ----------
RESET_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour


@api.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordBody, request: Request):
    """Always returns 200 with the same generic message to avoid user enumeration.
    If the email exists, we generate a one-time reset token and email it via Resend."""
    ip = _client_ip(request)
    # Throttle: 5 reset requests per hour per IP.
    await _check_auth_throttle(ip, "forgot_pw", max_attempts=5, window_seconds=3600,
                                label="password reset")
    await _record_auth_failure(ip, "forgot_pw", window_seconds=3600)

    email = body.email.lower().strip()
    user = await db.users.find_one({"email": email})
    # Also serve users who signed up via Google (no password_hash). For them
    # this becomes a "set password" flow rather than "reset" — but from the
    # caller's perspective it's the same endpoint + same email + same UI.
    if user:
        # Invalidate any prior unused tokens for this user so only the newest works.
        await db.password_reset_tokens.update_many(
            {"user_id": user["id"], "used": False},
            {"$set": {"used": True, "invalidated_at": utcnow()}},
        )
        raw_token = secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        now = utcnow()
        await db.password_reset_tokens.insert_one({
            "id": new_id(),
            "user_id": user["id"],
            "email": email,
            "token_hash": token_hash,
            "token_fp": _fingerprint(raw_token),
            "used": False,
            "created_at": now,
            "expires_at": now + timedelta(seconds=RESET_TOKEN_TTL_SECONDS),
            "requested_ip": ip,
        })
        public_url = os.environ.get("PUBLIC_APP_URL", "").rstrip("/") or "https://starqistna.com"
        reset_link = f"{public_url}/reset-password?token={raw_token}"
        try:
            await send_password_reset(
                to_email=email,
                full_name=user.get("full_name") or "there",
                reset_link=reset_link,
                ttl_minutes=RESET_TOKEN_TTL_SECONDS // 60,
            )
        except Exception as e:
            logger.warning("forgot-password email failed for %s: %s", email, e)
    return {"ok": True, "message": "If an account exists with that email, we've sent a reset link."}


@api.post("/auth/reset-password")
async def reset_password(body: ResetPasswordBody, request: Request):
    """Validate the reset token, apply the new password (strength-checked), mark token used."""
    ip = _client_ip(request)
    # Throttle token guessing: 10 per hour per IP.
    await _check_auth_throttle(ip, "reset_pw", max_attempts=10, window_seconds=3600,
                                label="password reset")

    _validate_password_strength(body.new_password)

    # O(1) lookup by fingerprint, then bcrypt-verify the single candidate (defence in depth).
    fp = _fingerprint(body.token)
    matched = await db.password_reset_tokens.find_one(
        {"token_fp": fp, "used": False, "expires_at": {"$gte": utcnow()}},
        {"_id": 0},
    )
    if matched and not bcrypt.checkpw(body.token.encode(), matched["token_hash"].encode()):
        matched = None  # fingerprint collision (vanishingly unlikely) → reject

    if not matched:
        await _record_auth_failure(ip, "reset_pw", window_seconds=3600)
        raise HTTPException(400, "This reset link is invalid or has expired. Please request a new one.")

    user = await db.users.find_one({"id": matched["user_id"]})
    if not user:
        raise HTTPException(400, "Account no longer exists.")

    new_hash = hash_password(body.new_password)
    await db.users.update_one({"id": user["id"]}, {"$set": {"password_hash": new_hash}})
    await db.password_reset_tokens.update_one(
        {"id": matched["id"]},
        {"$set": {"used": True, "used_at": utcnow()}},
    )
    # Clear any lingering login-throttle lockouts so the user can sign in immediately.
    try:
        await db.login_attempts.delete_many({"email": user["email"]})
    except Exception:
        pass
    return {"ok": True, "message": "Password updated. You can now sign in."}


@api.post("/auth/2fa/setup")
async def setup_2fa(body: TwoFASetupBody, user: dict = Depends(require_user)):
    # Re-verify password before generating secret
    full = await db.users.find_one({"id": user["id"]})
    if not full or not verify_password(body.password, full["password_hash"]):
        raise HTTPException(401, "Password check failed")
    if full.get("totp_enabled"):
        raise HTTPException(400, "2FA is already enabled")
    secret = pyotp.random_base32()
    await db.users.update_one({"id": user["id"]}, {"$set": {"totp_secret": secret, "totp_enabled": False}})
    issuer = "Qistna Express"
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name=issuer)
    return {"secret": secret, "otpauth_uri": uri, "issuer": issuer}


@api.post("/auth/2fa/enable")
async def enable_2fa(body: TwoFAEnableBody, user: dict = Depends(require_user)):
    full = await db.users.find_one({"id": user["id"]})
    if not full or not full.get("totp_secret"):
        raise HTTPException(400, "Start the setup flow first")
    if full.get("totp_enabled"):
        raise HTTPException(400, "2FA is already enabled")
    totp = pyotp.TOTP(full["totp_secret"])
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(401, "Invalid 2FA code — re-scan the QR and try again")
    await db.users.update_one({"id": user["id"]}, {"$set": {"totp_enabled": True}})
    return {"enabled": True}


@api.post("/auth/2fa/disable")
async def disable_2fa(body: TwoFADisableBody, user: dict = Depends(require_user)):
    full = await db.users.find_one({"id": user["id"]})
    if not full or not full.get("totp_enabled"):
        raise HTTPException(400, "2FA is not enabled")
    if not verify_password(body.password, full["password_hash"]):
        raise HTTPException(401, "Password check failed")
    totp = pyotp.TOTP(full["totp_secret"])
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(401, "Invalid 2FA code")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"totp_enabled": False}, "$unset": {"totp_secret": ""}},
    )
    return {"enabled": False}


@api.get("/auth/me")
async def me(user: dict = Depends(require_user)):
    # Enrich the /me payload with a `has_password` flag so the Dashboard can
    # show a "Set password" section for users who signed up via Google.
    full = await db.users.find_one({"id": user["id"]}, {"password_hash": 1})
    user["has_password"] = bool((full or {}).get("password_hash"))
    return user


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class SetPasswordBody(BaseModel):
    new_password: str = Field(min_length=8)


@api.post("/auth/set-password")
async def set_password(body: SetPasswordBody, user: dict = Depends(require_user)):
    """First-time password creation for accounts without one.

    Used by Google-authenticated users who want to also sign in with email +
    password (dual-auth). Users who already have a password must use
    ``/auth/change-password`` (which requires the current password).
    """
    full = await db.users.find_one({"id": user["id"]})
    if not full:
        raise HTTPException(404, "User not found")
    if full.get("password_hash"):
        raise HTTPException(
            409,
            detail={
                "message": "This account already has a password. Use change-password instead.",
                "code": "already_has_password",
            },
        )
    _validate_password_strength(body.new_password)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "password_hash": hash_password(body.new_password),
            "password_changed_at": utcnow().isoformat(),
        }},
    )
    return {
        "ok": True,
        "has_password": True,
        "message": "Password set. You can now sign in with email and password too.",
    }


@api.post("/auth/change-password")
async def change_password(body: ChangePasswordBody, user: dict = Depends(require_user)):
    """Authenticated user changes their own password.
    - Verifies current password (constant-time bcrypt check)
    - Enforces strength policy on the new password
    - Disallows reusing the current password
    - Clears the `must_change_password` flag on success
    """
    full = await db.users.find_one({"id": user["id"]})
    if not full or not full.get("password_hash"):
        raise HTTPException(400, "This account has no password set.")

    if not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(401, "Current password is incorrect.")

    if body.current_password == body.new_password:
        raise HTTPException(400, "New password must be different from the current one.")

    _validate_password_strength(body.new_password)

    new_hash = hash_password(body.new_password)
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_changed_at": utcnow().isoformat(),
            },
            "$unset": {"must_change_password": ""},
        },
    )
    return {"ok": True, "message": "Password updated. Please use the new password from now on."}


# ---------- Google Social Login (Own OAuth — starqistna.com branded) ----------
# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
# Flow (SPA-friendly authorization code):
#   1. Frontend redirects the user to accounts.google.com/o/oauth2/v2/auth
#      with our client_id, response_type=code, redirect_uri=<origin>/auth/google
#   2. Google redirects back to the frontend /auth/google?code=...
#   3. Frontend POSTs {code, redirect_uri} → this endpoint
#   4. Backend exchanges code + client_secret for tokens (server-to-server),
#      verifies the id_token signature via google-auth, upserts user, returns JWT.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


class GoogleCallbackBody(BaseModel):
    code: str
    redirect_uri: str


@api.post("/auth/google/callback")
async def google_oauth_callback(body: GoogleCallbackBody):
    """Exchange a Google authorization code for our own JWT.

    Auto-links to an existing account by email so a user who registered with
    email/password and then signs in with Google is merged (not duplicated).
    Respects 2FA: if the linked account has TOTP enabled we return a 2FA
    challenge instead of a session JWT.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google OAuth is not configured on the server.")

    # 1) Exchange authorization code for tokens.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": body.code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": body.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as e:
        logger.exception("Google token exchange failed")
        raise HTTPException(502, f"Google token exchange failed: {e}")

    if token_resp.status_code != 200:
        detail = token_resp.text[:200]
        logger.warning("Google token endpoint returned %s: %s", token_resp.status_code, detail)
        raise HTTPException(401, "Google authorization failed. Please try again.")

    token_data = token_resp.json()
    id_token_str = token_data.get("id_token")
    if not id_token_str:
        raise HTTPException(401, "Google did not return an id_token.")

    # 2) Verify id_token signature + audience.
    try:
        from google.oauth2 import id_token as g_id_token
        from google.auth.transport import requests as g_requests

        idinfo = g_id_token.verify_oauth2_token(
            id_token_str,
            g_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        logger.warning("Google id_token verification failed: %s", e)
        raise HTTPException(401, "Invalid Google identity token.")

    email = (idinfo.get("email") or "").lower().strip()
    if not email or not idinfo.get("email_verified", False):
        raise HTTPException(400, "Google account email is missing or unverified.")

    name = idinfo.get("name") or ""
    picture = idinfo.get("picture") or ""
    google_sub = idinfo.get("sub") or ""

    # 3) Upsert user (auto-link by email).
    existing = await db.users.find_one({"email": email})
    if existing:
        user_id = existing["id"]
        updates = {
            "google_linked": True,
            "google_sub": google_sub,
            "last_login_at": utcnow().isoformat(),
        }
        if name and not existing.get("full_name"):
            updates["full_name"] = name
        if picture and not existing.get("picture"):
            updates["picture"] = picture
        await db.users.update_one({"id": user_id}, {"$set": updates})
        user_doc = {**existing, **updates}
    else:
        user_id = new_id()
        user_doc = {
            "id": user_id,
            "email": email,
            "full_name": name,
            "phone": "",
            "password_hash": "",  # google-only accounts have no password
            "is_admin": False,
            "is_active": True,
            "google_linked": True,
            "google_sub": google_sub,
            "picture": picture,
            "created_at": utcnow().isoformat(),
            "last_login_at": utcnow().isoformat(),
        }
        await db.users.insert_one(user_doc)

    # 4) Enforce 2FA if enabled on the linked account.
    if user_doc.get("totp_enabled"):
        challenge = jwt.encode(
            {
                "sub": user_id,
                "email": email,
                "scope": "2fa_challenge",
                "exp": utcnow() + timedelta(minutes=5),
                "iat": utcnow(),
            },
            JWT_SECRET,
            algorithm=JWT_ALG,
        )
        return {"requires_2fa": True, "challenge_token": challenge}

    user_public = {k: v for k, v in user_doc.items() if k not in ("password_hash", "_id", "totp_secret")}
    user_public["has_password"] = bool(user_doc.get("password_hash"))
    return TokenResponse(access_token=issue_jwt(user_id, email), user=user_public)


# ---------- Seat locks (prevents double booking) ----------
@api.post("/seats/lock")
async def lock_seats(body: SeatLockBody, user: Optional[dict] = Depends(current_user)):
    """Attempts to lock given seats for 10 minutes using unique index. Returns failed seats if any."""
    sched = await db.schedules.find_one({"id": body.schedule_id})
    if not sched:
        raise HTTPException(404, "Schedule not found")

    lock_token = new_id()
    expires_at = utcnow() + timedelta(minutes=10)
    owner = user["id"] if user else f"guest:{lock_token}"

    acquired = []
    failed = []
    for sn in body.seat_numbers:
        # Try to insert; unique index on (schedule_id, seat_number) for non-released seats prevents duplicates.
        # Strategy: delete any expired locks for this seat first, then insert.
        await db.seat_locks.delete_many(
            {
                "schedule_id": body.schedule_id,
                "seat_number": sn,
                "status": "locked",
                "expires_at": {"$lt": utcnow().isoformat()},
            }
        )
        try:
            await db.seat_locks.insert_one(
                {
                    "id": new_id(),
                    "schedule_id": body.schedule_id,
                    "seat_number": sn,
                    "status": "locked",
                    "owner": owner,
                    "lock_token": lock_token,
                    "expires_at": expires_at.isoformat(),
                    "created_at": utcnow().isoformat(),
                }
            )
            acquired.append(sn)
        except DuplicateKeyError:
            failed.append(sn)

    if failed:
        # release what we locked
        await db.seat_locks.delete_many({"lock_token": lock_token, "status": "locked"})
        raise HTTPException(409, detail={"message": "Some seats unavailable", "failed_seats": failed})

    return {"lock_token": lock_token, "expires_at": expires_at.isoformat(), "seats": acquired}


@api.post("/seats/release")
async def release_seats(lock_token: str):
    await db.seat_locks.delete_many({"lock_token": lock_token, "status": "locked"})
    return {"released": True}


# ---------- Bookings ----------
def _calc_pricing(sched: dict, passengers: List[dict], promo: Optional[dict] = None) -> dict:
    adult_fare = float(sched["adult_fare"])
    # Prefer per-schedule child fare; fallback to half of adult for legacy schedules.
    child_fare = float(sched["child_fare"]) if sched.get("child_fare") is not None else round(adult_fare * 0.5, 2)
    adults = sum(1 for p in passengers if p["category"] == "adult")
    children = sum(1 for p in passengers if p["category"] == "child")
    subtotal = round(adults * adult_fare + children * child_fare, 2)

    discount = 0.0
    promo_info = None
    if promo:
        if promo["type"] == "percent":
            discount = round(subtotal * (promo["value"] / 100.0), 2)
        else:
            discount = min(round(float(promo["value"]), 2), subtotal)
        discount = max(0.0, min(discount, subtotal))
        promo_info = {
            "code": promo["code"],
            "type": promo["type"],
            "value": promo["value"],
            "discount_amount": discount,
        }

    total = round(subtotal - discount, 2)
    return {
        "adult_fare": adult_fare,
        "child_fare": child_fare,
        "adults": adults,
        "children": children,
        "subtotal": subtotal,
        "discount": discount,
        "promo": promo_info,
        "total": total,
        "currency": sched.get("currency", "myr"),
    }


async def _resolve_segment_pricing(sched: dict,
                                   pickup_terminal_id: Optional[str],
                                   dropoff_terminal_id: Optional[str]) -> dict:
    """Return a sched-shaped dict whose fare fields reflect the correct segment.

    - If the schedule has no linked route, returns `sched` untouched.
    - If the caller didn't specify pickup/dropoff, defaults to the schedule's own
      from/to (which for route-linked schedules is the route's origin→final).
    - If the requested segment isn't a valid route pairing → HTTP 400.
    """
    if not sched.get("route_id"):
        return sched
    # Route-linked. Default to the schedule's own from/to if caller didn't specify.
    pickup_id = pickup_terminal_id or sched["from_terminal_id"]
    dropoff_id = dropoff_terminal_id or sched["to_terminal_id"]
    # If the requested segment matches the schedule's own from/to and the schedule
    # already stores a fare, keep the schedule's fare (it was set at creation).
    if pickup_id == sched.get("from_terminal_id") and dropoff_id == sched.get("to_terminal_id"):
        return sched
    route = await db.routes.find_one({"id": sched["route_id"]}, {"_id": 0})
    if not route:
        raise HTTPException(400, "Linked route not found — cannot resolve segment fare.")
    pairing = next(
        (p for p in route.get("pairings", [])
         if p.get("pickup_id") == pickup_id and p.get("dropoff_id") == dropoff_id),
        None,
    )
    if not pairing:
        raise HTTPException(400, "Requested pickup/drop-off pair is not offered on this route.")
    # Return a shallow copy with the pairing's fare + currency baked in so that
    # `_calc_pricing` and the booking record reflect the sold segment.
    seg = dict(sched)
    seg["from_terminal_id"] = pickup_id
    seg["to_terminal_id"] = dropoff_id
    seg["adult_fare"] = float(pairing.get("adult_fare", sched.get("adult_fare")))
    seg["child_fare"] = float(pairing.get("child_fare", sched.get("child_fare", 0)))
    seg["currency"] = (pairing.get("currency") or sched.get("currency") or "myr").lower()
    return seg


async def _find_valid_promo(code: str, currency: str) -> dict:
    promo = await db.promo_codes.find_one({"code": code.upper().strip()}, {"_id": 0})
    if not promo:
        raise HTTPException(404, "Promo code not found")
    if not promo.get("active", True):
        raise HTTPException(400, "Promo code is inactive")
    if promo.get("currency") and promo["currency"].lower() != currency.lower():
        raise HTTPException(400, f"Promo code only valid for {promo['currency'].upper()}")
    if promo.get("valid_until"):
        try:
            if datetime.fromisoformat(promo["valid_until"]).date() < datetime.now(timezone.utc).date():
                raise HTTPException(400, "Promo code has expired")
        except ValueError:
            pass
    if promo.get("max_uses") is not None and promo.get("used_count", 0) >= promo["max_uses"]:
        raise HTTPException(400, "Promo code usage limit reached")
    return promo


# ---------- Promo Codes ----------
@api.post("/promo/validate")
async def validate_promo(body: PromoValidateBody):
    sched = await db.schedules.find_one({"id": body.schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")
    if (body.adults + body.children) != body.passenger_count:
        raise HTTPException(400, "Passenger breakdown mismatch")
    promo = await _find_valid_promo(body.code, sched.get("currency", "myr"))
    fake_pax = [{"category": "adult"}] * body.adults + [{"category": "child"}] * body.children
    pricing = _calc_pricing(sched, fake_pax, promo)
    return {"valid": True, "promo": pricing["promo"], "pricing": pricing}


@api.get("/admin/promo-codes")
async def list_promo_codes(user: dict = Depends(require_admin)):
    items = await db.promo_codes.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items


@api.post("/admin/promo-codes")
async def create_promo_code(body: PromoCreateBody, request: Request, user: dict = Depends(require_admin)):
    doc = body.model_dump()
    doc["code"] = doc["code"].upper().strip()
    existing = await db.promo_codes.find_one({"code": doc["code"]})
    if existing:
        raise HTTPException(400, "Code already exists")
    doc["id"] = new_id()
    doc["used_count"] = 0
    doc["created_at"] = utcnow().isoformat()
    await db.promo_codes.insert_one(doc)
    doc.pop("_id", None)
    await log_audit(user, "create", "promo_code", doc["id"],
                    {"code": doc["code"], "type": doc.get("type"), "value": doc.get("value")}, request)
    return doc


@api.patch("/admin/promo-codes/{code_id}")
async def toggle_promo_code(code_id: str, active: bool, request: Request, user: dict = Depends(require_admin)):
    result = await db.promo_codes.update_one({"id": code_id}, {"$set": {"active": active}})
    if result.matched_count == 0:
        raise HTTPException(404, "Promo code not found")
    await log_audit(user, "toggle", "promo_code", code_id, {"active": active}, request)
    return {"updated": True}


@api.delete("/admin/promo-codes/{code_id}")
async def delete_promo_code(code_id: str, request: Request, user: dict = Depends(require_admin)):
    existing = await db.promo_codes.find_one({"id": code_id}, {"_id": 0, "code": 1})
    result = await db.promo_codes.delete_one({"id": code_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Promo code not found")
    await log_audit(user, "delete", "promo_code", code_id,
                    {"code": (existing or {}).get("code")}, request)
    return {"deleted": True}


# ---------- Boarding gate validation (for existing QR readers) ----------
@api.post("/boarding/validate")
async def boarding_validate(body: BoardingValidateBody):
    """Endpoint for existing gate-scanners. QR encodes only the booking `reference` (plain text).
    The scanner posts the reference here; we return booking details and atomically mark as boarded."""
    ref = body.reference.strip().upper()
    booking = await db.bookings.find_one({"reference": ref}, {"_id": 0})
    if not booking:
        return {"valid": False, "reason": "not_found", "reference": ref}
    if booking.get("status") != "confirmed":
        return {"valid": False, "reason": "not_confirmed", "status": booking.get("status"), "reference": ref}
    sched = await db.schedules.find_one({"id": booking["schedule_id"]}, {"_id": 0})
    # Already boarded?
    already = bool(booking.get("boarded_at"))
    if body.mark_boarded and not already:
        await db.bookings.update_one(
            {"reference": ref},
            {"$set": {"boarded_at": utcnow().isoformat(), "boarded_gate": body.gate}},
        )
        booking["boarded_at"] = utcnow().isoformat()
        booking["boarded_gate"] = body.gate
    return {
        "valid": True,
        "already_boarded": already,
        "reference": ref,
        "schedule": sched,
        "passengers": booking.get("passengers", []),
        "seats": booking.get("seats", []),
        "from_terminal_id": booking.get("from_terminal_id"),
        "to_terminal_id": booking.get("to_terminal_id"),
        "departure_date": booking.get("departure_date"),
        "departure_time": booking.get("departure_time"),
        "boarded_at": booking.get("boarded_at"),
    }


@api.post("/bookings")
async def create_booking(body: CreateBookingBody, user: Optional[dict] = Depends(current_user)):
    sched = await db.schedules.find_one({"id": body.schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")

    # Block new bookings on a schedule the operator has retired via end_date.
    # Existing bookings on the same schedule remain untouched.
    end_date = (sched.get("end_date") or "").strip()
    if end_date and end_date < sched.get("departure_date", ""):
        raise HTTPException(
            410,
            detail={
                "message": "This departure is no longer available for new bookings.",
                "code": "schedule_retired",
            },
        )

    if len(body.passengers) != len(body.seat_assignments):
        raise HTTPException(400, "Passengers and seat assignments must match in count")

    seats = [sa["seat_number"] for sa in body.seat_assignments]
    # verify locks exist (any owner) and aren't booked yet -- must belong to this session ideally
    current_locks = await db.seat_locks.find(
        {"schedule_id": body.schedule_id, "seat_number": {"$in": seats}},
        {"_id": 0},
    ).to_list(50)
    lock_map = {lk["seat_number"]: lk for lk in current_locks}
    missing = [s for s in seats if s not in lock_map or lock_map[s]["status"] != "locked"]
    if missing:
        raise HTTPException(409, detail={"message": "Seat locks missing or expired", "seats": missing})

    # Resolve the specific segment (pickup/drop-off + fare) — for route-linked
    # schedules this returns a sched-shaped dict with pairing fares; otherwise
    # returns `sched` unchanged.
    segment = await _resolve_segment_pricing(
        sched, body.pickup_terminal_id, body.dropoff_terminal_id,
    )

    pricing_promo = None
    if body.promo_code:
        pricing_promo = await _find_valid_promo(body.promo_code, segment.get("currency", "myr"))
    pricing = _calc_pricing(segment, [p.model_dump() for p in body.passengers], pricing_promo)

    booking_id = new_id()
    # Build passenger <-> seat mapping
    pax_with_seat = []
    for sa in body.seat_assignments:
        idx = sa["passenger_index"]
        p = body.passengers[idx].model_dump()
        p["seat_number"] = sa["seat_number"]
        pax_with_seat.append(p)

    booking_ref = "SQ" + booking_id.replace("-", "")[:8].upper()
    doc = {
        "id": booking_id,
        "reference": booking_ref,
        "schedule_id": body.schedule_id,
        # The from/to on the booking always reflect the SOLD segment (pickup/drop-off
        # the passenger actually chose), NOT the physical bus endpoints.
        "from_terminal_id": segment["from_terminal_id"],
        "to_terminal_id": segment["to_terminal_id"],
        "departure_date": sched["departure_date"],
        "departure_time": sched["departure_time"],
        "user_id": user["id"] if user else None,
        "contact_email": body.contact_email.lower(),
        "contact_phone": body.contact_phone,
        "passengers": pax_with_seat,
        "seats": seats,
        "pricing": pricing,
        "status": "pending_payment",
        "payment_status": "pending",
        "created_at": utcnow().isoformat(),
    }
    await db.bookings.insert_one(doc)

    # Attach booking_id to locks
    await db.seat_locks.update_many(
        {"schedule_id": body.schedule_id, "seat_number": {"$in": seats}},
        {"$set": {"booking_id": booking_id}},
    )

    return {k: v for k, v in doc.items() if k != "_id"}


@api.get("/bookings/me")
async def my_bookings(user: dict = Depends(require_user)):
    items = await db.bookings.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    if not items:
        return items
    # Batch-fetch all referenced terminals in a single query (replaces N+1 lookups).
    term_ids = {b["from_terminal_id"] for b in items} | {b["to_terminal_id"] for b in items}
    terms = {
        t["id"]: t
        async for t in db.terminals.find({"id": {"$in": list(term_ids)}}, {"_id": 0})
    }
    for b in items:
        b["from"] = terms.get(b["from_terminal_id"])
        b["to"] = terms.get(b["to_terminal_id"])
    return items


@api.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: str,
    email: Optional[str] = None,
    user: Optional[dict] = Depends(current_user),
):
    """Allow:
      - the booking owner (authenticated user_id match)
      - any admin
      - a guest who knows id + the contact_email used at checkout (?email=)
    """
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")

    is_owner = bool(user and b.get("user_id") and user.get("id") == b["user_id"])
    is_admin = bool(user and user.get("is_admin"))
    is_guest_match = bool(
        email
        and b.get("contact_email")
        and email.strip().lower() == b["contact_email"].strip().lower()
    )
    if not (is_owner or is_admin or is_guest_match):
        raise HTTPException(403, "You don't have access to this booking")

    b["from"] = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
    b["to"] = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
    b["schedule"] = await db.schedules.find_one({"id": b["schedule_id"]}, {"_id": 0})
    return b


# ---------- Cancellation ----------
# Policy (confirmed by ops): cancel ≥24h before departure → full refund.
#                           cancel <24h before departure → ticket burned (no refund).
CANCEL_REFUND_THRESHOLD_HOURS = 24


def _hours_to_departure(booking: dict) -> float:
    """Hours between now and the booking's departure (MY/SG local time, UTC+8)."""
    try:
        dep_local = datetime.fromisoformat(f"{booking['departure_date']}T{booking['departure_time']}:00")
        dep_utc = dep_local.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        return (dep_utc - utcnow()).total_seconds() / 3600.0
    except Exception:
        return 0.0


@api.get("/bookings/{booking_id}/cancellation-quote")
async def cancellation_quote(booking_id: str, user: dict = Depends(require_user)):
    """Tell the user, before they confirm, exactly what cancelling now will do."""
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "Not your booking")
    if b.get("status") != "confirmed":
        raise HTTPException(400, f"Cannot cancel a booking in status '{b.get('status')}'")
    hours = _hours_to_departure(b)
    eligible = hours >= CANCEL_REFUND_THRESHOLD_HOURS
    pricing = b.get("pricing", {})

    # Stripe charged in MYR — quote the refund in the actual billed currency
    # so the customer sees on the modal what will appear on their statement.
    refund_amount = 0.0
    refund_currency = (pricing.get("currency") or "myr").upper()
    if eligible and float(pricing.get("total", 0)) > 0:
        txn = await db.payment_transactions.find_one(
            {"booking_id": booking_id, "payment_status": "paid"},
            sort=[("created_at", -1)],
        )
        if txn:
            refund_amount = float(txn.get("amount", 0))
            refund_currency = (txn.get("currency") or "myr").upper()
        else:
            refund_amount = float(pricing.get("total", 0))
    return {
        "booking_id": b["id"],
        "reference": b.get("reference"),
        "hours_to_departure": round(hours, 2),
        "threshold_hours": CANCEL_REFUND_THRESHOLD_HOURS,
        "refund_eligible": eligible,
        "refund_amount": refund_amount,
        "currency": refund_currency,
        "outcome": "full_refund" if eligible else "ticket_burned",
        "policy": "Free cancellation up to 24 hours before departure. Within 24 hours, the ticket is non-refundable.",
    }


def _stripe_refund_sync(payment_intent_id: str, amount_minor_units: int, idem_key: str) -> dict:
    """Run the blocking Stripe refund call inside a worker thread."""
    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_API_KEY
    return stripe_sdk.Refund.create(
        payment_intent=payment_intent_id,
        amount=amount_minor_units,
        reason="requested_by_customer",
        idempotency_key=idem_key,
    )


def _stripe_get_payment_intent_sync(session_id: str) -> Optional[str]:
    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_API_KEY
    sess = stripe_sdk.checkout.Session.retrieve(session_id)
    pi = sess.get("payment_intent") if isinstance(sess, dict) else getattr(sess, "payment_intent", None)
    return pi if isinstance(pi, str) else None


@api.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, user: dict = Depends(require_user)):
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "Not your booking")
    if b.get("status") != "confirmed":
        raise HTTPException(400, f"Cannot cancel a booking in status '{b.get('status')}'")

    hours = _hours_to_departure(b)
    eligible = hours >= CANCEL_REFUND_THRESHOLD_HOURS
    pricing = b.get("pricing", {})
    currency = (pricing.get("currency") or "myr").lower()
    total = float(pricing.get("total", 0))
    refund_info = {"refunded": False, "amount": 0.0, "currency": currency.upper(), "stripe_refund_id": None}

    if eligible and total > 0:
        # Pull the most recent paid txn for this booking
        txn = await db.payment_transactions.find_one(
            {"booking_id": booking_id, "payment_status": "paid"},
            sort=[("created_at", -1)],
        )
        if not txn or not txn.get("session_id"):
            raise HTTPException(400, "No paid Stripe session found for this booking; cannot refund automatically.")
        # Refund must use the actual amount + currency Stripe charged (MYR),
        # NOT the booking's display total — which may be in SGD.
        charged_amount = float(txn.get("amount", total))
        charged_currency = (txn.get("currency") or "myr").upper()
        try:
            pi = await asyncio.to_thread(_stripe_get_payment_intent_sync, txn["session_id"])
            if not pi:
                raise HTTPException(400, "Stripe session has no payment_intent yet.")
            # Stripe expects amounts in minor units (cents/sen)
            amount_minor = int(round(charged_amount * 100))
            idem_key = f"refund:{booking_id}"
            refund = await asyncio.to_thread(_stripe_refund_sync, pi, amount_minor, idem_key)
            refund_info = {
                "refunded": True,
                "amount": charged_amount,
                "currency": charged_currency,
                "stripe_refund_id": refund.get("id") if isinstance(refund, dict) else getattr(refund, "id", None),
            }
            await db.payment_transactions.update_one(
                {"session_id": txn["session_id"]},
                {"$set": {"payment_status": "refunded", "refunded_at": utcnow().isoformat(),
                          "refund_amount": charged_amount, "refund_id": refund_info["stripe_refund_id"]}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Stripe refund failed for booking %s: %s", booking_id, e)
            raise HTTPException(502, f"Refund failed: {e}")
    elif eligible and total == 0:
        # Free booking (e.g. 100% promo): no money to refund, but cancellation is still
        # "eligible" — treat as a clean cancellation, not a burn.
        refund_info = {"refunded": True, "amount": 0.0, "currency": currency.upper(), "stripe_refund_id": None}

    # Free up the seats so they can be re-sold
    await db.seat_locks.delete_many({"booking_id": booking_id})

    new_status = "cancelled_refunded" if refund_info["refunded"] else "cancelled_burned"
    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {
            "status": new_status,
            "cancelled_at": utcnow().isoformat(),
            "cancelled_by_user_id": user["id"],
            "cancellation_outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
            "cancellation_refund": refund_info,
        }},
    )

    # Best-effort email
    try:
        from_term = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
        to_term = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
        from email_service import send_booking_cancelled
        asyncio.create_task(send_booking_cancelled(b, from_term, to_term, refund_info["refunded"], refund_info["amount"], refund_info["currency"]))
    except Exception as e:
        logger.warning("Cancellation email queue failed: %s", e)

    log_audit_task = log_audit(user, "cancel", "booking", booking_id, {
        "outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
        "hours_to_departure": round(hours, 2),
        "amount": refund_info["amount"],
        "currency": refund_info["currency"],
    })
    asyncio.create_task(log_audit_task)

    return {
        "ok": True,
        "booking_id": booking_id,
        "status": new_status,
        "outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
        "refund": refund_info,
        "hours_to_departure": round(hours, 2),
    }


# ---------- Payments (Stripe) ----------
@lru_cache(maxsize=8)
def _payment_options_for(currency: str) -> list:
    """Return list of payment method options based on currency.
    All methods are processed through Stripe using the same merchant account.
    Option `id` maps directly to Stripe `payment_method_types`.
    - Card: universal (Visa/Mastercard/Amex) — all currencies
    - GrabPay: MY + SG markets (MYR, SGD)
    - FPX: MY online banking (MYR only)
    Cached because (a) input space is tiny (myr/sgd/usd) and (b) result is read-only.
    """
    c = (currency or "myr").lower()
    opts = [
        {
            "id": "card",
            "name": "Credit / Debit Card",
            "provider": "Stripe",
            "methods": ["Visa", "Mastercard", "Amex"],
            "available": True,
            "note": None,
        },
        {
            "id": "grabpay",
            "name": "GrabPay",
            "provider": "Stripe",
            "methods": ["GrabPay wallet"],
            "available": c in ("myr", "sgd"),
            "note": None if c in ("myr", "sgd") else "Not available for this currency",
        },
    ]
    if c == "myr":
        opts.append({
            "id": "fpx",
            "name": "FPX Online Banking",
            "provider": "Stripe",
            "methods": ["Maybank", "CIMB", "Public Bank", "RHB", "+ all Malaysian banks"],
            "available": True,
            "note": None,
        })
    return opts


@api.get("/payments/options/{booking_id}")
async def payment_options(
    booking_id: str,
    email: Optional[str] = None,
    user: Optional[dict] = Depends(current_user),
):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")

    is_owner = bool(user and booking.get("user_id") and user.get("id") == booking["user_id"])
    is_admin = bool(user and user.get("is_admin"))
    is_guest_match = bool(
        email
        and booking.get("contact_email")
        and email.strip().lower() == booking["contact_email"].strip().lower()
    )
    if not (is_owner or is_admin or is_guest_match):
        raise HTTPException(403, "You don't have access to this booking")

    currency = booking.get("pricing", {}).get("currency", "myr")
    return {
        "booking_id": booking_id,
        "currency": currency,
        "amount": booking.get("pricing", {}).get("total", 0),
        "options": _payment_options_for(currency),
    }


@api.post("/payments/checkout")
async def create_checkout(body: CheckoutBody, request: Request, user: Optional[dict] = Depends(current_user)):
    booking = await db.bookings.find_one({"id": body.booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["payment_status"] == "paid":
        raise HTTPException(400, "Booking already paid")

    # Single Stripe account — Malaysia. Always bill in MYR regardless of where
    # the trip departs from. Display currency on receipts can stay as-is (SGD
    # for SG departures), but the actual charge is converted to MYR using the
    # live sgd_to_myr_rate maintained in app settings.
    BILLING_CURRENCY = "myr"
    display_currency = (booking["pricing"].get("currency") or "myr").lower()
    display_total = float(booking["pricing"]["total"])
    if display_currency == BILLING_CURRENCY:
        amount = display_total
        fx_rate = 1.0
    else:
        # Currently we only support SGD → MYR. Anything else is misconfigured.
        if display_currency != "sgd":
            raise HTTPException(400, f"Cannot bill {display_currency.upper()} bookings — only MYR/SGD supported.")
        settings = await _get_settings()
        fx_rate = float(settings.get("sgd_to_myr_rate", DEFAULT_SGD_TO_MYR))
        amount = round(display_total * fx_rate, 2)

    # Gateway availability is now driven by the BILLING currency, not display.
    available_ids = {o["id"] for o in _payment_options_for(BILLING_CURRENCY) if o["available"]}
    if body.gateway not in available_ids:
        raise HTTPException(400, f"Payment method '{body.gateway}' is not available")

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)

    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&booking_id={body.booking_id}"
    cancel_url = f"{origin}/payment/cancel?booking_id={body.booking_id}"

    checkout_req = CheckoutSessionRequest(
        amount=amount,
        currency=BILLING_CURRENCY,
        success_url=success_url,
        cancel_url=cancel_url,
        payment_methods=[body.gateway],  # card | grabpay | fpx
        metadata={
            "booking_id": body.booking_id,
            "booking_reference": booking["reference"],
            "user_id": booking.get("user_id") or "guest",
            "payment_method": body.gateway,
            "display_currency": display_currency,
            "display_total": str(display_total),
            "fx_rate": str(fx_rate),
        },
    )
    try:
        session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)
    except Exception as e:
        # Stripe surfaces payment-method activation issues with a helpful message
        # (e.g. "The payment method type provided: grabpay is invalid"). Return
        # the exact message so the frontend can guide the operator to enable it
        # in the Stripe dashboard instead of showing a generic error.
        msg = str(e)
        logger.warning("Stripe create_checkout_session failed: %s", msg)
        if "invalid" in msg.lower() and body.gateway.lower() in msg.lower():
            raise HTTPException(
                422,
                detail={
                    "message": f"Payment method '{body.gateway}' isn't activated on this Stripe account yet. "
                               f"Enable it under Stripe Dashboard → Settings → Payment methods, "
                               f"or select Card to continue.",
                    "gateway": body.gateway,
                    "stripe_error": msg,
                },
            )
        raise HTTPException(502, detail={"message": f"Could not start Stripe checkout: {msg}"})

    await db.payment_transactions.insert_one(
        {
            "id": new_id(),
            "session_id": session.session_id,
            "booking_id": body.booking_id,
            "amount": amount,                  # MYR — what Stripe actually charges
            "currency": BILLING_CURRENCY,
            "display_amount": display_total,   # original price as shown to customer
            "display_currency": display_currency,
            "fx_rate": fx_rate,
            "payment_method": body.gateway,
            "user_id": booking.get("user_id"),
            "user_email": booking["contact_email"],
            "payment_status": "initiated",
            "status": "initiated",
            "metadata": {"booking_reference": booking["reference"], "payment_method": body.gateway},
            "created_at": utcnow().isoformat(),
            "updated_at": utcnow().isoformat(),
        }
    )

    return {"url": session.url, "session_id": session.session_id}


@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request):
    txn = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not txn:
        raise HTTPException(404, "Transaction not found")

    # If already finalized, return current state
    if txn.get("payment_status") == "paid" and txn.get("booking_finalized"):
        booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
        return {"payment_status": "paid", "status": txn.get("status"), "booking": booking}

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    try:
        check: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    except Exception as e:
        logger.warning("Stripe status fetch failed for %s: %s", session_id, e)
        booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
        return {
            "payment_status": txn.get("payment_status", "unknown"),
            "status": txn.get("status", "unknown"),
            "booking": booking,
            "error": "stripe_status_unavailable",
        }

    updates = {
        "status": check.status,
        "payment_status": check.payment_status,
        "updated_at": utcnow().isoformat(),
    }
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": updates})

    # Finalize booking idempotently
    if check.payment_status == "paid" and not txn.get("booking_finalized"):
        await _finalize_booking(txn["booking_id"], session_id)

    booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
    return {"payment_status": check.payment_status, "status": check.status, "booking": booking}


# ---------- Stuck-payment reconciliation --------------------------------
# The webhook is our primary "payment confirmed" signal, but Stripe can
# throttle/disable it (e.g. after transient 5xxs) and async methods like
# GrabPay can settle after the client-side polling window has closed.
# The reconciler is a belt-and-braces safety net: it re-asks Stripe about
# every `initiated` transaction older than RECONCILE_MIN_AGE_SECONDS and
# finalizes any that Stripe now reports as paid.
RECONCILE_MIN_AGE_SECONDS = 30
RECONCILE_MAX_AGE_SECONDS = 60 * 60 * 24  # ignore anything older than 24h
RECONCILE_INTERVAL_SECONDS = 60


async def _reconcile_one_transaction(txn: dict) -> dict:
    """Ask Stripe for the current status of a single session and finalize
    the booking if it's now paid. Returns a short summary dict for logging."""
    session_id = txn.get("session_id")
    if not session_id:
        return {"session_id": None, "action": "skip_missing_session_id"}
    if txn.get("booking_finalized"):
        return {"session_id": session_id, "action": "already_finalized"}

    # Reuse a fresh StripeCheckout — no request needed here.
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url="")
    try:
        check: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("reconcile: stripe status fetch failed for %s: %s", session_id, e)
        return {"session_id": session_id, "action": "stripe_error", "error": str(e)[:200]}

    await db.payment_transactions.update_one(
        {"session_id": session_id},
        {"$set": {
            "status": check.status,
            "payment_status": check.payment_status,
            "updated_at": utcnow().isoformat(),
        }},
    )
    if check.payment_status == "paid":
        await _finalize_booking(txn["booking_id"], session_id)
        logger.info("reconcile: FINALIZED booking for session=%s", session_id)
        return {
            "session_id": session_id, "booking_id": txn.get("booking_id"),
            "action": "finalized", "stripe_status": check.status,
        }
    return {
        "session_id": session_id, "booking_id": txn.get("booking_id"),
        "action": "still_pending", "stripe_status": check.status,
        "stripe_payment_status": check.payment_status,
    }


async def _reconcile_stuck_transactions() -> list[dict]:
    """Scan for `initiated` transactions in the 30s..24h age band and reconcile each."""
    now = utcnow()
    min_created = (now - timedelta(seconds=RECONCILE_MAX_AGE_SECONDS)).isoformat()
    max_created = (now - timedelta(seconds=RECONCILE_MIN_AGE_SECONDS)).isoformat()
    cursor = db.payment_transactions.find({
        "booking_finalized": {"$ne": True},
        "payment_status": {"$ne": "paid"},
        "created_at": {"$gte": min_created, "$lte": max_created},
    }, {"_id": 0}).limit(50)
    results: list[dict] = []
    async for txn in cursor:
        try:
            results.append(await _reconcile_one_transaction(txn))
        except Exception as e:  # noqa: BLE001
            logger.exception("reconcile: unexpected error for %s: %s", txn.get("session_id"), e)
    return results


async def _reconcile_loop() -> None:
    """Background task: reconcile stuck payments every RECONCILE_INTERVAL_SECONDS."""
    while True:
        try:
            outcomes = await _reconcile_stuck_transactions()
            actions = [o.get("action") for o in outcomes]
            finalized = sum(1 for a in actions if a == "finalized")
            if outcomes:
                logger.info("reconcile: scanned=%d finalized=%d", len(outcomes), finalized)
        except Exception as e:  # noqa: BLE001
            logger.exception("reconcile: loop error: %s", e)
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)


@api.post("/admin/payments/reconcile")
async def admin_reconcile_payments(user: dict = Depends(require_admin)):
    """Force an immediate reconciliation sweep of all initiated transactions.

    Handy after the Stripe webhook has been disabled or the client-side
    polling window closed before an async payment (GrabPay/FPX) settled.
    """
    outcomes = await _reconcile_stuck_transactions()
    return {
        "checked": len(outcomes),
        "finalized": sum(1 for o in outcomes if o.get("action") == "finalized"),
        "results": outcomes,
    }


@api.post("/admin/payments/reconcile/{session_id}")
async def admin_reconcile_one(session_id: str, user: dict = Depends(require_admin)):
    """Force reconciliation for a single Stripe session — useful when a
    specific customer complains their paid booking is stuck."""
    txn = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not txn:
        raise HTTPException(404, "Transaction not found")
    return await _reconcile_one_transaction(txn)


async def _issue_gohub_tickets(booking_id: str) -> None:
    """After payment confirms, call CTS `getOnlineQR_V2` to fetch the real QR
    ticket(s) for a booking. Fire-and-forget: never crash the caller.

    Result is persisted onto the booking as:
      * ``gohub_status``     — "confirmed" | "skipped" | "failed"
      * ``gohub_tickets``    — [{seat_number, opetickno, tickno, qr}, ...]
      * ``gohub_error``      — {code, message}  (only when status == failed)
      * ``gohub_processed_at`` — ISO timestamp

    When ``GOHUB_ENABLED`` is off, or the from/to terminals lack a ``cts_code``,
    or the schedule lacks a ``trip_no``, we mark it ``skipped`` — the operator
    can retry from the admin panel once TBS finishes their config.

    Finally, always dispatches the booking-confirmation email (regardless of
    CTS outcome) so the customer always receives a receipt.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        logger.warning("gohub: booking %s vanished before ticket issuance", booking_id)
        return

    now_iso = utcnow().isoformat()

    # Load everything we need for the CTS call in parallel.
    schedule, from_term, to_term = await asyncio.gather(
        db.schedules.find_one({"id": booking.get("schedule_id")}, {"_id": 0}),
        db.terminals.find_one({"id": booking.get("from_terminal_id")}, {"_id": 0}),
        db.terminals.find_one({"id": booking.get("to_terminal_id")}, {"_id": 0}),
    )

    # Pre-flight validation — anything missing means we skip, not fail.
    reasons: list[str] = []
    if os.environ.get("GOHUB_ENABLED", "false").strip().lower() not in ("1", "true", "yes"):
        reasons.append("GOHUB_ENABLED=false")
    if not schedule or not (schedule.get("trip_no") or "").strip():
        reasons.append("schedule.trip_no missing")
    if not from_term or not (from_term.get("cts_code") or "").strip():
        reasons.append("from_terminal.cts_code missing")
    if not to_term or not (to_term.get("cts_code") or "").strip():
        reasons.append("to_terminal.cts_code missing")

    async def _send_email_with_current_state() -> None:
        """Reload the booking (so the email sees the latest gohub_* fields)
        and fire the confirmation email — never lets errors escape."""
        try:
            fresh = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
            if fresh:
                await send_booking_confirmation(fresh, from_term, to_term)
        except Exception:  # noqa: BLE001
            logger.exception("gohub: email dispatch failed for booking %s", booking_id)

    if reasons:
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "skipped",
                "gohub_skip_reason": "; ".join(reasons),
                "gohub_processed_at": now_iso,
            }},
        )
        logger.info("gohub: skipped booking %s (%s)", booking.get("reference"), ", ".join(reasons))
        await _send_email_with_current_state()
        return

    from gohub_client import GoHubClient, GoHubError, make_opetickno

    booking_ref = booking.get("reference") or booking_id[:8].upper()
    # Adult and child fares live on the schedule; use them as sprice per seattype.
    adult_fare = float(schedule.get("adult_fare") or 0.0)
    child_fare = float(schedule.get("child_fare") or (adult_fare * 0.5))

    # Build one CTS <detail> per passenger. CTS caps opetickno at 20 chars —
    # make_opetickno enforces that and raises early if it would overflow.
    seats: list[dict] = []
    for pax in booking.get("passengers", []):
        seat_no = str(pax.get("seat_number") or "").strip()
        category = (pax.get("category") or "adult").lower()
        seattype = "C" if category == "child" else "A"
        seats.append({
            "opetickno": make_opetickno(booking_ref, seat_no),
            "seatno": seat_no,
            "seattype": seattype,
            "sprice": child_fare if seattype == "C" else adult_fare,
            "name": (pax.get("name") or "").strip()[:50] or "Passenger",
            "ic": (pax.get("ic_or_passport") or "").strip()[:14],
            "contact": (booking.get("contact_phone") or "").strip()[:14],
        })

    client = GoHubClient(db=db, timeout=20)
    try:
        result = await client.get_qr(
            trans_id=booking_ref,
            trip_no=schedule["trip_no"],
            trip_date=schedule["departure_date"],
            depart_date=schedule["departure_date"],
            depart_time=schedule["departure_time"],
            from_counter=from_term["cts_code"],
            to_counter=to_term["cts_code"],
            seats=seats,
        )
    except GoHubError as e:
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "failed",
                "gohub_error": {"code": e.code, "message": e.message},
                "gohub_processed_at": now_iso,
            }},
        )
        logger.warning("gohub: booking %s failed [%s] %s", booking_ref, e.code, e.message)
        await _send_email_with_current_state()
        return
    except Exception as e:  # noqa: BLE001 — never propagate to caller
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "failed",
                "gohub_error": {"code": "UNEXPECTED", "message": str(e)[:250]},
                "gohub_processed_at": now_iso,
            }},
        )
        logger.exception("gohub: unexpected error for booking %s", booking_ref)
        await _send_email_with_current_state()
        return

    # Parse per-passenger detail rows out of the flattened response.
    # `_parse_response` flattens `<detail .../>` children as ``detail_<attr>``
    # keys — since CTS returns MULTIPLE `<detail>` we surface only the last
    # one via the flattened dict. The full XML lives in ``gohub_logs``.
    #
    # For every seat we also fetch the CTS-branded QR image (with gopass logo)
    # from their separate image endpoint — in parallel to keep this fast.
    # If any image fetch fails, we simply don't store one and the frontend
    # falls back to a locally-generated plain QR.
    raw_qr = result.get("detail_QR") or result.get("QR") or result.get("qr") or ""
    image_bytes_list = await asyncio.gather(
        *(client.fetch_qr_image(raw_qr) for _ in seats)
    ) if raw_qr else [None] * len(seats)

    tickets: list[dict] = []
    for seat, img_bytes in zip(seats, image_bytes_list):
        ticket_entry = {
            "seat_number": seat["seatno"],
            "opetickno": seat["opetickno"],
            "tickno": result.get("detail_tickno") or result.get("tickno") or "",
            "qr": raw_qr,
        }
        if img_bytes:
            import base64 as _b64
            ticket_entry["qr_image"] = "data:image/png;base64," + _b64.b64encode(img_bytes).decode("ascii")
        tickets.append(ticket_entry)

    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {
            "gohub_status": "confirmed",
            "gohub_tickets": tickets,
            "gohub_processed_at": now_iso,
        }, "$unset": {"gohub_error": ""}},
    )
    logger.info("gohub: booking %s issued %d ticket(s)", booking_ref, len(tickets))
    await _send_email_with_current_state()


async def _finalize_booking(booking_id: str, session_id: str):
    """Idempotent: mark seats booked, confirm booking, increment promo use, mark txn finalized.

    The confirmation email is dispatched from inside ``_issue_gohub_tickets``
    once the CTS attempt completes — so the email always reflects the final
    boarding-pass state (real CTS QR vs. fallback).
    """
    await db.seat_locks.update_many(
        {"booking_id": booking_id, "status": "locked"},
        {"$set": {"status": "booked", "expires_at": None}},
    )
    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {"status": "confirmed", "payment_status": "paid", "paid_at": utcnow().isoformat()}},
    )
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    promo = (booking or {}).get("pricing", {}).get("promo")
    if promo and promo.get("code"):
        await db.promo_codes.update_one({"code": promo["code"]}, {"$inc": {"used_count": 1}})
    await db.payment_transactions.update_one(
        {"session_id": session_id},
        {"$set": {"booking_finalized": True, "payment_status": "paid", "status": "complete"}},
    )
    if booking:
        # Fire-and-forget CTS QR issuance — includes the confirmation email dispatch.
        asyncio.create_task(_issue_gohub_tickets(booking_id))


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """
    Stripe webhook receiver.

    Contract with Stripe:
      • Signature-verification failure  -> 400 (Stripe will retry with backoff, then disable).
      • Anything else                   -> 200 (even on business errors, so Stripe never disables us).
        Business errors are logged and can be reconciled from the Stripe dashboard.
    """
    body_bytes = await request.body()
    sig = request.headers.get("Stripe-Signature") or request.headers.get("stripe-signature")

    # 1. Parse + verify signature.
    event_dict: Optional[dict] = None
    if STRIPE_WEBHOOK_SECRET:
        if not sig:
            logger.warning("stripe_webhook: missing Stripe-Signature header")
            return JSONResponse(status_code=400, content={"error": "missing_signature"})
        try:
            event = stripe_sdk.Webhook.construct_event(
                body_bytes, sig, STRIPE_WEBHOOK_SECRET
            )
            event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        except stripe_sdk.error.SignatureVerificationError as e:
            logger.warning("stripe_webhook: signature verification failed: %s", e)
            return JSONResponse(status_code=400, content={"error": "invalid_signature"})
        except ValueError as e:
            # Invalid payload — return 400 so Stripe knows the body is malformed.
            logger.warning("stripe_webhook: invalid payload: %s", e)
            return JSONResponse(status_code=400, content={"error": "invalid_payload"})
        except Exception as e:
            # Any other verification-layer error: log and return 400 (do not fail 500).
            logger.exception("stripe_webhook: verification error: %s", e)
            return JSONResponse(status_code=400, content={"error": "verification_error"})
    else:
        # No secret configured (dev/preview). Parse JSON but skip verification.
        logger.warning(
            "stripe_webhook: STRIPE_WEBHOOK_SECRET is not set — accepting event without verification"
        )
        try:
            import json as _json
            event_dict = _json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            logger.exception("stripe_webhook: could not parse payload: %s", e)
            # Still return 200 so Stripe stops retrying garbage.
            return JSONResponse(status_code=200, content={"ok": True, "warning": "unparsable"})

    # 2. From here on, ALWAYS return 200 — business errors must not disable the endpoint.
    try:
        event_type = event_dict.get("type") if isinstance(event_dict, dict) else None
        event_id = event_dict.get("id") if isinstance(event_dict, dict) else None
        obj = (event_dict or {}).get("data", {}).get("object", {}) or {}

        session_id: Optional[str] = None
        payment_status: Optional[str] = None

        if event_type == "checkout.session.completed":
            session_id = obj.get("id")
            payment_status = obj.get("payment_status")
        elif event_type == "checkout.session.async_payment_succeeded":
            session_id = obj.get("id")
            payment_status = "paid"
        elif event_type == "checkout.session.async_payment_failed":
            session_id = obj.get("id")
            payment_status = "failed"
        elif event_type == "checkout.session.expired":
            session_id = obj.get("id")
            payment_status = obj.get("payment_status") or "expired"
        elif event_type == "payment_intent.succeeded":
            session_id = (obj.get("metadata") or {}).get("checkout_session_id")
            payment_status = "paid"
        elif event_type == "payment_intent.payment_failed":
            session_id = (obj.get("metadata") or {}).get("checkout_session_id")
            payment_status = "failed"
        else:
            # Any other event type: acknowledge and move on.
            logger.info("stripe_webhook: ignoring event type=%s id=%s", event_type, event_id)
            return JSONResponse(status_code=200, content={"ok": True, "ignored": event_type})

        logger.info(
            "stripe_webhook: type=%s session=%s payment_status=%s",
            event_type, session_id, payment_status,
        )

        # Finalize booking (idempotent). Guard every DB call.
        if payment_status == "paid" and session_id:
            try:
                txn = await db.payment_transactions.find_one({"session_id": session_id})
                if not txn:
                    logger.warning(
                        "stripe_webhook: no payment_transaction row for session=%s (webhook fired before checkout row?)",
                        session_id,
                    )
                elif txn.get("booking_finalized"):
                    logger.info("stripe_webhook: session=%s already finalized — skipping", session_id)
                else:
                    await _finalize_booking(txn["booking_id"], session_id)
                    logger.info("stripe_webhook: finalized booking for session=%s", session_id)
            except Exception as be:
                # Never let finalisation errors escape as 5xx to Stripe.
                logger.exception(
                    "stripe_webhook: finalize_booking failed for session=%s: %s", session_id, be
                )

        elif payment_status in ("failed", "expired") and session_id:
            try:
                await db.payment_transactions.update_one(
                    {"session_id": session_id},
                    {"$set": {"payment_status": payment_status, "updated_at": utcnow().isoformat()}},
                )
            except Exception as be:
                logger.exception(
                    "stripe_webhook: could not update payment_transaction for session=%s: %s",
                    session_id, be,
                )

        return JSONResponse(status_code=200, content={"ok": True, "event": event_type})

    except Exception as outer:
        # Absolute last-resort net: never 5xx to Stripe.
        logger.exception("stripe_webhook: unexpected error: %s", outer)
        return JSONResponse(status_code=200, content={"ok": True, "warning": "handler_error"})


# ---------- Admin ----------
@api.get("/admin/stats")
async def admin_stats(user: dict = Depends(require_admin)):
    # Run all 5 counts concurrently — ~5× faster than sequential awaits.
    users_n, bookings_n, confirmed_n, terminals_n, schedules_n = await asyncio.gather(
        db.users.count_documents({}),
        db.bookings.count_documents({}),
        db.bookings.count_documents({"status": "confirmed"}),
        db.terminals.count_documents({}),
        db.schedules.count_documents({}),
    )
    return {
        "users": users_n,
        "bookings": bookings_n,
        "confirmed_bookings": confirmed_n,
        "terminals": terminals_n,
        "schedules": schedules_n,
    }


@api.get("/admin/bookings")
async def admin_bookings(user: dict = Depends(require_admin)):
    items = await db.bookings.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items


@api.get("/admin/payments")
async def admin_payments(
    status_filter: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """List Stripe payment transactions joined with booking reference + customer email."""
    query: dict = {}
    if status_filter and status_filter != "all":
        query["payment_status"] = status_filter

    items = await db.payment_transactions.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)

    # Aggregate summary
    summary = {
        "total": len(items),
        "paid": 0,
        "initiated": 0,
        "failed": 0,
        "refunded": 0,
        "gross_myr": 0.0,
        "gross_sgd": 0.0,
    }
    for t in items:
        ps = (t.get("payment_status") or "initiated").lower()
        if ps == "paid":
            summary["paid"] += 1
            ccy = (t.get("currency") or "myr").lower()
            key = "gross_myr" if ccy == "myr" else "gross_sgd" if ccy == "sgd" else None
            if key:
                summary[key] += float(t.get("amount") or 0)
        elif ps == "failed":
            summary["failed"] += 1
        elif ps in ("refunded", "canceled", "expired"):
            summary["refunded"] += 1
        else:
            summary["initiated"] += 1

    return {"summary": summary, "items": items}


@api.get("/admin/audit-logs")
async def admin_audit_logs(
    limit: int = 200,
    resource: Optional[str] = None,
    action: Optional[str] = None,
    actor_email: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """List admin audit log entries, newest first."""
    query: dict = {}
    if resource and resource != "all":
        query["resource"] = resource
    if action and action != "all":
        query["action"] = action
    if actor_email:
        safe_email = re.escape(actor_email.strip()[:120])
        query["actor_email"] = {"$regex": safe_email, "$options": "i"}
    limit = max(1, min(limit, 1000))
    items = await db.audit_logs.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"total": len(items), "items": items}


# ---------- Admin management ----------
ADMIN_INVITE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _admin_public(u: dict) -> dict:
    """Strip secrets before returning an admin record."""
    return {
        "id": u.get("id"),
        "email": u.get("email"),
        "full_name": u.get("full_name"),
        "role": u.get("role") or ("super_admin" if u.get("email") == "admin@starqistna.com" else "admin"),
        "is_active": u.get("is_active", True),
        "created_at": u.get("created_at"),
        "last_login_at": u.get("last_login_at"),
    }


@api.get("/admin/admins")
async def list_admins(user: dict = Depends(require_admin)):
    """Any admin may view the admin roster."""
    users = await db.users.find({"is_admin": True}, {"_id": 0}).sort("created_at", 1).to_list(200)
    invites = await db.admin_invites.find(
        {"accepted_at": None, "revoked_at": None, "expires_at": {"$gte": utcnow()}},
        {"_id": 0, "token_hash": 0},
    ).sort("created_at", -1).to_list(100)
    return {
        "admins": [_admin_public(u) for u in users],
        "pending_invites": invites,
    }


@api.post("/admin/admins/invite")
async def invite_admin(body: AdminInviteBody, request: Request, user: dict = Depends(require_super_admin)):
    email = body.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing and existing.get("is_admin") and existing.get("is_active", True):
        raise HTTPException(400, "This user is already an active admin")

    # Expire any prior open invites for this email
    await db.admin_invites.update_many(
        {"email": email, "accepted_at": None, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )

    raw_token = secrets.token_urlsafe(32)
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
    now = utcnow()
    invite_id = new_id()
    await db.admin_invites.insert_one({
        "id": invite_id,
        "email": email,
        "full_name": body.full_name.strip(),
        "role": body.role,
        "token_hash": token_hash,
        "token_fp": _fingerprint(raw_token),
        "invited_by_id": user["id"],
        "invited_by_email": user["email"],
        "created_at": now,
        "expires_at": now + timedelta(seconds=ADMIN_INVITE_TTL_SECONDS),
        "accepted_at": None,
        "revoked_at": None,
    })

    public_url = os.environ.get("PUBLIC_APP_URL", "").rstrip("/") or "https://starqistna.com"
    accept_link = f"{public_url}/admin-invite?token={raw_token}"
    try:
        await send_admin_invite(
            to_email=email,
            full_name=body.full_name,
            inviter_name=user.get("full_name") or user.get("email"),
            role=body.role,
            accept_link=accept_link,
            ttl_days=ADMIN_INVITE_TTL_SECONDS // 86400,
        )
    except Exception as e:
        logger.warning("admin invite email failed for %s: %s", email, e)

    await log_audit(user, "invite", "admin", invite_id,
                    {"email": email, "role": body.role}, request)
    return {"ok": True, "invite_id": invite_id, "expires_in_days": ADMIN_INVITE_TTL_SECONDS // 86400}


@api.post("/admin/admins/accept-invite")
async def accept_admin_invite(body: AdminAcceptInviteBody, request: Request):
    """Public endpoint. Bcrypt-compare token, upsert the user as admin, set password."""
    ip = _client_ip(request)
    await _check_auth_throttle(ip, "admin_invite_accept", max_attempts=10, window_seconds=3600,
                                label="admin invite acceptance")

    _validate_password_strength(body.password)

    fp = _fingerprint(body.token)
    matched = await db.admin_invites.find_one(
        {"token_fp": fp, "accepted_at": None, "revoked_at": None,
         "expires_at": {"$gte": utcnow()}},
        {"_id": 0},
    )
    if matched and not bcrypt.checkpw(body.token.encode(), matched["token_hash"].encode()):
        matched = None  # fingerprint collision → reject
    if not matched:
        await _record_auth_failure(ip, "admin_invite_accept", window_seconds=3600)
        raise HTTPException(400, "Invite link is invalid, already used, or expired.")

    email = matched["email"]
    pw_hash = hash_password(body.password)
    now = utcnow()
    existing = await db.users.find_one({"email": email})
    if existing:
        await db.users.update_one(
            {"id": existing["id"]},
            {"$set": {
                "password_hash": pw_hash,
                "is_admin": True,
                "role": matched["role"],
                "is_active": True,
                "full_name": existing.get("full_name") or matched["full_name"],
            }},
        )
        user_id = existing["id"]
    else:
        user_id = new_id()
        await db.users.insert_one({
            "id": user_id,
            "email": email,
            "full_name": matched["full_name"],
            "phone": None,
            "password_hash": pw_hash,
            "is_admin": True,
            "role": matched["role"],
            "is_active": True,
            "created_at": now.isoformat(),
        })

    await db.admin_invites.update_one(
        {"id": matched["id"]},
        {"$set": {"accepted_at": now, "accepted_user_id": user_id}},
    )
    # Audit this as a system action (no authenticated actor at this point).
    await log_audit({"id": user_id, "email": email}, "accept_invite", "admin", user_id,
                    {"role": matched["role"], "invited_by": matched.get("invited_by_email")}, request)

    return {"ok": True, "email": email}


@api.patch("/admin/admins/{admin_id}/role")
async def update_admin_role(admin_id: str, body: AdminRoleUpdateBody, request: Request,
                             user: dict = Depends(require_super_admin)):
    target = await db.users.find_one({"id": admin_id, "is_admin": True})
    if not target:
        raise HTTPException(404, "Admin not found")

    if target["id"] == user["id"] and body.role != "super_admin":
        raise HTTPException(400, "You cannot demote yourself — ask another super-admin.")

    # Prevent demoting the last super-admin
    if body.role != "super_admin" and target.get("role") == "super_admin":
        remaining = await db.users.count_documents({
            "is_admin": True, "role": "super_admin", "is_active": {"$ne": False},
            "id": {"$ne": admin_id},
        })
        if remaining == 0:
            raise HTTPException(400, "Cannot demote the last super-admin.")

    await db.users.update_one({"id": admin_id}, {"$set": {"role": body.role}})
    await log_audit(user, "update_role", "admin", admin_id,
                    {"email": target["email"], "new_role": body.role}, request)
    return {"ok": True}


@api.patch("/admin/admins/{admin_id}/active")
async def update_admin_active(admin_id: str, body: AdminActiveUpdateBody, request: Request,
                               user: dict = Depends(require_super_admin)):
    target = await db.users.find_one({"id": admin_id, "is_admin": True})
    if not target:
        raise HTTPException(404, "Admin not found")

    if target["id"] == user["id"] and not body.is_active:
        raise HTTPException(400, "You cannot deactivate yourself.")

    # Prevent deactivating the last active super-admin
    if not body.is_active and target.get("role") == "super_admin":
        remaining = await db.users.count_documents({
            "is_admin": True, "role": "super_admin",
            "is_active": {"$ne": False},
            "id": {"$ne": admin_id},
        })
        if remaining == 0:
            raise HTTPException(400, "Cannot deactivate the last active super-admin.")

    await db.users.update_one({"id": admin_id}, {"$set": {"is_active": body.is_active}})
    await log_audit(user, "deactivate" if not body.is_active else "reactivate",
                    "admin", admin_id, {"email": target["email"]}, request)
    return {"ok": True}


@api.delete("/admin/admins/{admin_id}")
async def revoke_admin(admin_id: str, request: Request, user: dict = Depends(require_super_admin)):
    """Revoke admin rights (keeps the user account, flips is_admin to False)."""
    target = await db.users.find_one({"id": admin_id, "is_admin": True})
    if not target:
        raise HTTPException(404, "Admin not found")

    if target["id"] == user["id"]:
        raise HTTPException(400, "You cannot revoke your own admin access.")

    if target.get("role") == "super_admin":
        remaining = await db.users.count_documents({
            "is_admin": True, "role": "super_admin",
            "is_active": {"$ne": False},
            "id": {"$ne": admin_id},
        })
        if remaining == 0:
            raise HTTPException(400, "Cannot revoke the last super-admin.")

    await db.users.update_one({"id": admin_id}, {"$set": {"is_admin": False, "role": None}})
    await log_audit(user, "revoke", "admin", admin_id, {"email": target["email"]}, request)
    return {"ok": True}


@api.delete("/admin/admins/invites/{invite_id}")
async def cancel_admin_invite(invite_id: str, request: Request, user: dict = Depends(require_super_admin)):
    result = await db.admin_invites.update_one(
        {"id": invite_id, "accepted_at": None, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Invite not found or already handled")
    await log_audit(user, "cancel_invite", "admin", invite_id, {}, request)
    return {"ok": True}



# ---------- App settings (currency rate, etc.) ----------
DEFAULT_SGD_TO_MYR = 3.50


class AppSettingsBody(BaseModel):
    sgd_to_myr_rate: float = Field(gt=0, le=20)


async def _get_settings() -> dict:
    doc = await db.app_settings.find_one({"id": "global"}, {"_id": 0})
    if not doc:
        doc = {
            "id": "global",
            "sgd_to_myr_rate": DEFAULT_SGD_TO_MYR,
            "updated_at": utcnow().isoformat(),
        }
        await db.app_settings.insert_one(doc)
    return doc


@api.get("/settings")
async def public_settings():
    """Public — used by the frontend to compute the bracketed MYR approx for SGD trips."""
    if "global" in _settings_cache:
        return _settings_cache["global"]
    s = await _get_settings()
    payload = {"sgd_to_myr_rate": s.get("sgd_to_myr_rate", DEFAULT_SGD_TO_MYR)}
    _settings_cache["global"] = payload
    return payload


@api.patch("/admin/settings")
async def update_settings(body: AppSettingsBody, request: Request, user: dict = Depends(require_admin)):
    await db.app_settings.update_one(
        {"id": "global"},
        {"$set": {"sgd_to_myr_rate": body.sgd_to_myr_rate, "updated_at": utcnow().isoformat()}},
        upsert=True,
    )
    _settings_cache.pop("global", None)  # bust cache so next /settings reads fresh
    await log_audit(user, "update", "settings", "global",
                    {"sgd_to_myr_rate": body.sgd_to_myr_rate}, request)
    return {"ok": True, "sgd_to_myr_rate": body.sgd_to_myr_rate}



# ---------- Customer feedback ----------
FEEDBACK_ADMIN_EMAIL = "admin@starqistna.com"


def _feedback_ref() -> str:
    """Short, human-friendly reference e.g. F-7X2M9K."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars
    return "F-" + "".join(secrets.choice(alphabet) for _ in range(6))


@api.post("/feedback")
async def submit_feedback(body: FeedbackBody, request: Request):
    ip = _client_ip(request)
    # Throttle: 3/hour/IP
    await _check_auth_throttle(ip, "feedback", max_attempts=3, window_seconds=3600,
                                label="feedback")
    await _record_auth_failure(ip, "feedback", window_seconds=3600)

    # Authenticated? link it
    user_id = None
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth.split()[1], JWT_SECRET, algorithms=[JWT_ALG])
            user_id = payload.get("sub")
        except Exception:
            pass

    ref = _feedback_ref()
    now = utcnow()
    doc = {
        "id": new_id(),
        "reference": ref,
        "name": (body.name or "").strip() or None,
        "email": body.email.lower().strip(),
        "category": body.category,
        "booking_reference": (body.booking_reference or "").strip().upper() or None,
        "rating": body.rating,
        "message": body.message.strip(),
        "status": "new",
        "user_id": user_id,
        "ip": ip,
        "created_at": now.isoformat(),
        "resolved_at": None,
    }
    await db.feedback.insert_one(doc)
    doc.pop("_id", None)

    # Fire-and-forget emails
    try:
        await send_feedback_confirmation(
            to_email=doc["email"],
            name=doc["name"] or "there",
            reference=ref,
            category=doc["category"],
            message=doc["message"],
        )
    except Exception as e:
        logger.warning("feedback confirmation email failed: %s", e)
    try:
        await send_feedback_admin_notification(
            admin_email=FEEDBACK_ADMIN_EMAIL,
            feedback=doc,
        )
    except Exception as e:
        logger.warning("feedback admin notification failed: %s", e)

    return {"ok": True, "reference": ref}


@api.get("/admin/feedback")
async def admin_list_feedback(
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    query: dict = {}
    if status_filter and status_filter != "all":
        query["status"] = status_filter
    if category and category != "all":
        query["category"] = category
    items = await db.feedback.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    # Run summary counts concurrently
    total_n, new_n, prog_n, done_n = await asyncio.gather(
        db.feedback.count_documents({}),
        db.feedback.count_documents({"status": "new"}),
        db.feedback.count_documents({"status": "in_progress"}),
        db.feedback.count_documents({"status": "resolved"}),
    )
    summary = {
        "total": total_n,
        "new": new_n,
        "in_progress": prog_n,
        "resolved": done_n,
    }
    return {"summary": summary, "items": items}


@api.patch("/admin/feedback/{feedback_id}")
async def admin_update_feedback_status(feedback_id: str, body: FeedbackStatusBody, request: Request,
                                        user: dict = Depends(require_admin)):
    existing = await db.feedback.find_one({"id": feedback_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Feedback not found")
    updates: dict = {"status": body.status}
    if body.status == "resolved":
        updates["resolved_at"] = utcnow()
        updates["resolved_by"] = user.get("email")
    await db.feedback.update_one({"id": feedback_id}, {"$set": updates})
    await log_audit(user, "update_status", "feedback", feedback_id,
                    {"status": body.status, "reference": existing.get("reference")}, request)
    return {"ok": True}






@api.get("/admin/schedules")
async def admin_schedules(user: dict = Depends(require_admin)):
    items = await db.schedules.find({}, {"_id": 0}).sort("departure_date", 1).to_list(500)
    return items


class CreateScheduleBody(BaseModel):
    from_terminal_id: str
    to_terminal_id: str
    departure_date: str
    departure_time: str
    arrival_time: str
    bus_operator: str = "Qistna Express"
    bus_type: str = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)
    currency: str = "myr"
    # Optional link to the Routes system (P0 wiring). If provided, the schedule
    # is treated as an instance of that route — customer flow will use the
    # route's pickup/dropoff pairings for pricing + boarding UX.
    route_id: Optional[str] = None
    trip_no: Optional[str] = None  # CTS/GoHub trip identifier, max 10 chars


class ScheduleUpdateBody(BaseModel):
    departure_date: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    bus_operator: Optional[str] = None
    bus_type: Optional[str] = None
    adult_fare: Optional[float] = None
    child_fare: Optional[float] = None
    total_seats: Optional[int] = Field(default=None, ge=12, le=60)
    route_id: Optional[str] = None
    trip_no: Optional[str] = None
    # Last date this schedule series is valid. Set (or extend) this via the
    # edit modal to retire a trip without deleting rows and losing bookings.
    # Same field that `BulkCreateScheduleBody.end_date` already writes at
    # creation time — reused here so there's exactly one "trip stop date"
    # concept in the system.  Empty string CLEARS a previously-set value.
    end_date: Optional[str] = None
    # When true AND the update changes departure_date or departure_time,
    # send a notification email to every confirmed passenger on this schedule.
    notify_passengers: bool = False


class BulkScheduleBody(BaseModel):
    from_terminal_id: str
    to_terminal_id: str
    start_date: str  # YYYY-MM-DD  (first trip)
    end_date: str    # YYYY-MM-DD  (last trip — inclusive)
    days_of_week: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon..6=Sun
    departure_time: str
    arrival_time: str
    bus_type: str = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)
    # Optional link to a Route (same rationale as CreateScheduleBody).
    route_id: Optional[str] = None
    trip_no: Optional[str] = None


class BulkDeleteScheduleFilter(BaseModel):
    """Filter for bulk delete previews and executions.

    All fields are optional — combine to narrow the match. At least one filter
    MUST be provided or the request is rejected (safety guard).
    """
    from_terminal_id: Optional[str] = None
    to_terminal_id: Optional[str] = None
    start_date: Optional[str] = None  # YYYY-MM-DD (inclusive)
    end_date: Optional[str] = None    # YYYY-MM-DD (inclusive)
    route_id: Optional[str] = None
    unlinked_only: bool = False       # schedules where route_id is null / missing
    force: bool = False               # allow deleting even schedules that have bookings


# (AppSettingsBody is defined alongside the settings endpoints earlier in the file.)


class TerminalCreateBody(BaseModel):
    city: str = Field(min_length=1)
    name: str = Field(min_length=1)
    code: str = Field(min_length=2, max_length=8)
    state: Optional[str] = None
    country: Literal["MY", "SG"] = "MY"
    # New optional fields — used by the routes builder to display map + generate GoHub QRs.
    landmark_address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    cts_code: Optional[str] = None
    is_pickup: bool = True
    is_dropoff: bool = True


class TerminalUpdateBody(BaseModel):
    city: Optional[str] = None
    name: Optional[str] = None
    code: Optional[str] = None
    state: Optional[str] = None
    country: Optional[Literal["MY", "SG"]] = None
    landmark_address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    cts_code: Optional[str] = None
    is_pickup: Optional[bool] = None
    is_dropoff: Optional[bool] = None


@api.get("/admin/terminals")
async def admin_list_terminals(user: dict = Depends(require_admin)):
    items = await db.terminals.find({}, {"_id": 0}).sort("city", 1).to_list(500)
    # attach schedule counts for delete safety
    for t in items:
        t["schedule_count"] = await db.schedules.count_documents(
            {"$or": [{"from_terminal_id": t["id"]}, {"to_terminal_id": t["id"]}]}
        )
    return items


@api.post("/admin/terminals")
async def admin_create_terminal(body: TerminalCreateBody, request: Request, user: dict = Depends(require_admin)):
    code = body.code.upper().strip()
    if await db.terminals.find_one({"code": code}):
        raise HTTPException(400, f"Terminal code {code} already exists")
    doc = {
        "id": new_id(),
        "city": body.city.strip(),
        "name": body.name.strip(),
        "code": code,
        "state": (body.state or "").strip() or None,
        "country": body.country,
        "landmark_address": (body.landmark_address or "").strip() or None,
        "lat": body.lat,
        "lng": body.lng,
        "cts_code": (body.cts_code or "").strip() or None,
        "is_pickup": body.is_pickup,
        "is_dropoff": body.is_dropoff,
    }
    await db.terminals.insert_one(doc)
    doc.pop("_id", None)
    _terminals_cache.clear()  # bust cache so /api/terminals reflects the new row
    await log_audit(user, "create", "terminal", doc["id"],
                    {"code": doc["code"], "city": doc["city"], "name": doc["name"]}, request)
    return doc


@api.patch("/admin/terminals/{terminal_id}")
async def admin_update_terminal(terminal_id: str, body: TerminalUpdateBody, request: Request, user: dict = Depends(require_admin)):
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "code" in updates:
        updates["code"] = updates["code"].upper().strip()
        clash = await db.terminals.find_one({"code": updates["code"], "id": {"$ne": terminal_id}})
        if clash:
            raise HTTPException(400, "Another terminal already uses this code")
    for field in ("landmark_address", "cts_code"):
        if field in updates and isinstance(updates[field], str):
            updates[field] = updates[field].strip() or None
    if not updates:
        raise HTTPException(400, "No changes provided")
    result = await db.terminals.update_one({"id": terminal_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Terminal not found")
    _terminals_cache.clear()
    updated = await db.terminals.find_one({"id": terminal_id}, {"_id": 0})
    await log_audit(user, "update", "terminal", terminal_id, {"changes": updates}, request)
    return updated


@api.delete("/admin/terminals/{terminal_id}")
async def admin_delete_terminal(terminal_id: str, request: Request, user: dict = Depends(require_admin)):
    # Block delete if any schedule references this terminal
    count = await db.schedules.count_documents(
        {"$or": [{"from_terminal_id": terminal_id}, {"to_terminal_id": terminal_id}]}
    )
    if count > 0:
        raise HTTPException(400, f"Cannot delete — {count} schedule(s) reference this terminal")
    existing = await db.terminals.find_one({"id": terminal_id}, {"_id": 0, "code": 1, "city": 1})
    result = await db.terminals.delete_one({"id": terminal_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Terminal not found")
    _terminals_cache.clear()
    await log_audit(user, "delete", "terminal", terminal_id,
                    {"code": (existing or {}).get("code"), "city": (existing or {}).get("city")}, request)
    return {"deleted": True}


# ---------- Routes (city-to-city trip templates with pickup/dropoff pairings) ----------
DEFAULT_FARE = 55.0


class RouteStopIn(BaseModel):
    """A pickup or dropoff stop referenced from a route.

    `terminal_id` refers to a row in `terminals`. `offset_min` is minutes from
    the schedule's `departure_time` — used to compute the actual clock-time
    for each stop without hard-coding absolute times on the route.
    """
    terminal_id: str
    offset_min: int = Field(ge=0, le=48 * 60)  # cap at 48h so a typo doesn't nuke the UI


class RoutePairingIn(BaseModel):
    pickup_id: str            # terminal_id of a boarding stop
    dropoff_id: str           # terminal_id of an alighting stop
    adult_fare: float = Field(ge=0, default=DEFAULT_FARE)
    child_fare: float = Field(ge=0, default=DEFAULT_FARE)
    senior_fare: float = Field(ge=0, default=DEFAULT_FARE)
    oku_fare: float = Field(ge=0, default=DEFAULT_FARE)
    currency: Literal["myr", "sgd"] = "myr"
    cts_route_code: Optional[str] = None


class RouteCreateBody(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1)
    origin_city: str = Field(min_length=1)
    destination_city: str = Field(min_length=1)
    direction: Optional[str] = None   # e.g. "KL-SG" or "SG-KL" (free text)
    boarding_stops: List[RouteStopIn] = []
    alighting_stops: List[RouteStopIn] = []
    pairings: List[RoutePairingIn] = []
    is_active: bool = True


class RouteUpdateBody(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    direction: Optional[str] = None
    boarding_stops: Optional[List[RouteStopIn]] = None
    alighting_stops: Optional[List[RouteStopIn]] = None
    pairings: Optional[List[RoutePairingIn]] = None
    is_active: Optional[bool] = None


def _serialize_route(doc: dict) -> dict:
    doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


async def _validate_route_stops(body: RouteCreateBody | RouteUpdateBody) -> None:
    """Ensure all referenced terminal_ids exist and pairings reference valid stops."""
    boarding = body.boarding_stops or []
    alighting = body.alighting_stops or []
    pairings = body.pairings or []

    ids = {s.terminal_id for s in boarding} | {s.terminal_id for s in alighting}
    if ids:
        found = await db.terminals.count_documents({"id": {"$in": list(ids)}})
        if found != len(ids):
            raise HTTPException(400, "One or more stop terminal_ids do not exist.")

    boarding_ids = {s.terminal_id for s in boarding}
    alighting_ids = {s.terminal_id for s in alighting}
    for p in pairings:
        if p.pickup_id not in boarding_ids:
            raise HTTPException(400, f"Pairing pickup {p.pickup_id!r} not in boarding_stops.")
        if p.dropoff_id not in alighting_ids:
            raise HTTPException(400, f"Pairing dropoff {p.dropoff_id!r} not in alighting_stops.")


@api.get("/admin/routes")
async def admin_list_routes(user: dict = Depends(require_admin)):
    items = await db.routes.find({}, {"_id": 0}).sort("code", 1).to_list(500)
    return items


@api.get("/admin/routes/{route_id}")
async def admin_get_route(route_id: str, user: dict = Depends(require_admin)):
    doc = await db.routes.find_one({"id": route_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Route not found")
    return doc


@api.post("/admin/routes")
async def admin_create_route(body: RouteCreateBody, request: Request, user: dict = Depends(require_admin)):
    code = body.code.upper().strip()
    if await db.routes.find_one({"code": code}):
        raise HTTPException(400, f"Route code {code} already exists.")
    await _validate_route_stops(body)
    doc = body.model_dump()
    doc["code"] = code
    doc["id"] = new_id()
    doc["created_at"] = utcnow().isoformat()
    doc["updated_at"] = doc["created_at"]
    await db.routes.insert_one(doc)
    await log_audit(user, "create", "route", doc["id"],
                    {"code": code, "name": body.name}, request)
    return _serialize_route(doc)


@api.patch("/admin/routes/{route_id}")
async def admin_update_route(route_id: str, body: RouteUpdateBody, request: Request, user: dict = Depends(require_admin)):
    existing = await db.routes.find_one({"id": route_id})
    if not existing:
        raise HTTPException(404, "Route not found")
    updates = body.model_dump(exclude_unset=True)
    if "code" in updates:
        updates["code"] = updates["code"].upper().strip()
        clash = await db.routes.find_one({"code": updates["code"], "id": {"$ne": route_id}})
        if clash:
            raise HTTPException(400, "Another route already uses this code.")
    if any(k in updates for k in ("boarding_stops", "alighting_stops", "pairings")):
        merged = {**existing, **updates}
        await _validate_route_stops(RouteCreateBody(**{
            "code": merged.get("code", existing["code"]),
            "name": merged.get("name", existing["name"]),
            "origin_city": merged.get("origin_city", existing["origin_city"]),
            "destination_city": merged.get("destination_city", existing["destination_city"]),
            "direction": merged.get("direction"),
            "boarding_stops": merged.get("boarding_stops", []),
            "alighting_stops": merged.get("alighting_stops", []),
            "pairings": merged.get("pairings", []),
            "is_active": merged.get("is_active", True),
        }))
    updates["updated_at"] = utcnow().isoformat()
    result = await db.routes.update_one({"id": route_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Route not found")
    doc = await db.routes.find_one({"id": route_id}, {"_id": 0})
    await log_audit(user, "update", "route", route_id,
                    {"fields": list(updates.keys())}, request)
    return doc


@api.delete("/admin/routes/{route_id}")
async def admin_delete_route(route_id: str, request: Request, user: dict = Depends(require_admin)):
    ref = await db.schedules.count_documents({"route_id": route_id})
    if ref > 0:
        raise HTTPException(400, f"Cannot delete — {ref} schedule(s) reference this route.")
    existing = await db.routes.find_one({"id": route_id}, {"_id": 0, "code": 1})
    result = await db.routes.delete_one({"id": route_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Route not found")
    await log_audit(user, "delete", "route", route_id,
                    {"code": (existing or {}).get("code")}, request)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bus Types — simple catalog of vehicle presets (name + seat count + image)
# so admins pick a bus in one click instead of typing seat counts from memory.
# ---------------------------------------------------------------------------

class CreateBusTypeBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    seat_count: int = Field(ge=12, le=60)
    image_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=200)


class UpdateBusTypeBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    seat_count: Optional[int] = Field(default=None, ge=12, le=60)
    image_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=200)


@api.get("/bus-types")
async def list_bus_types_public():
    """Public read — used by the schedule form so admins can pick a bus.
    Ordered by name for predictable dropdown listing."""
    items = await db.bus_types.find({}, {"_id": 0}).sort("name", 1).to_list(200)
    return items


@api.get("/admin/bus-types")
async def admin_list_bus_types(_: dict = Depends(require_admin)):
    items = await db.bus_types.find({}, {"_id": 0}).sort("name", 1).to_list(200)
    return items


@api.post("/admin/bus-types")
async def admin_create_bus_type(body: CreateBusTypeBody, request: Request, user: dict = Depends(require_admin)):
    name = body.name.strip()
    if await db.bus_types.find_one({"name": name}):
        raise HTTPException(400, "A bus type with this name already exists.")
    doc = {
        "id": new_id(),
        "name": name,
        "seat_count": body.seat_count,
        "image_url": (body.image_url or "").strip() or None,
        "description": (body.description or "").strip() or None,
        "layout": _layout_for_bus_type(name),
        "created_at": utcnow().isoformat(),
    }
    await db.bus_types.insert_one(doc)
    await log_audit(user, "create", "bus_type", doc["id"],
                    {"name": name, "seat_count": body.seat_count}, request)
    return {k: v for k, v in doc.items() if k != "_id"}


@api.patch("/admin/bus-types/{bus_type_id}")
async def admin_update_bus_type(bus_type_id: str, body: UpdateBusTypeBody, request: Request, user: dict = Depends(require_admin)):
    updates: dict = {}
    if body.name is not None:
        new_name = body.name.strip()
        clash = await db.bus_types.find_one({"name": new_name, "id": {"$ne": bus_type_id}})
        if clash:
            raise HTTPException(400, "Another bus type already uses this name.")
        updates["name"] = new_name
        updates["layout"] = _layout_for_bus_type(new_name)
    if body.seat_count is not None:
        updates["seat_count"] = body.seat_count
    if body.image_url is not None:
        updates["image_url"] = body.image_url.strip() or None
    if body.description is not None:
        updates["description"] = body.description.strip() or None
    if not updates:
        raise HTTPException(400, "No fields to update.")
    result = await db.bus_types.update_one({"id": bus_type_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Bus type not found")
    doc = await db.bus_types.find_one({"id": bus_type_id}, {"_id": 0})
    await log_audit(user, "update", "bus_type", bus_type_id,
                    {"fields": list(updates.keys())}, request)
    return doc


@api.delete("/admin/bus-types/{bus_type_id}")
async def admin_delete_bus_type(bus_type_id: str, request: Request, user: dict = Depends(require_admin)):
    existing = await db.bus_types.find_one({"id": bus_type_id}, {"_id": 0, "name": 1})
    if not existing:
        raise HTTPException(404, "Bus type not found")
    # Block deletion if any schedule still references this bus type by name.
    in_use = await db.schedules.count_documents({"bus_type": existing["name"]})
    if in_use > 0:
        raise HTTPException(400, f"Cannot delete — {in_use} schedule(s) still use '{existing['name']}'.")
    await db.bus_types.delete_one({"id": bus_type_id})
    await log_audit(user, "delete", "bus_type", bus_type_id,
                    {"name": existing["name"]}, request)
    return {"deleted": True}


@api.post("/admin/schedules")
async def admin_create_schedule(body: CreateScheduleBody, request: Request, user: dict = Depends(require_admin)):
    # If a route is linked, force from = first boarding stop, to = last alighting
    # stop. This encodes the rule: one schedule = one physical bus running the
    # full route. Sub-segments become bookable via the search flow.
    if body.route_id:
        route = await db.routes.find_one({"id": body.route_id}, {"_id": 0})
        if not route:
            raise HTTPException(400, "Linked route_id does not exist")
        boarding = sorted(route.get("boarding_stops", []) or [], key=lambda s: s.get("offset_min", 0))
        alighting = sorted(route.get("alighting_stops", []) or [], key=lambda s: s.get("offset_min", 0))
        if not boarding or not alighting:
            raise HTTPException(400, "Linked route must have at least one boarding and one alighting stop.")
        body.from_terminal_id = boarding[0]["terminal_id"]
        body.to_terminal_id = alighting[-1]["terminal_id"]
    # Auto-derive currency from origin terminal country (SG → SGD, else MYR)
    origin = await db.terminals.find_one({"id": body.from_terminal_id}, {"_id": 0})
    if not origin:
        raise HTTPException(400, "Invalid from_terminal_id")
    derived_currency = COUNTRY_TO_CURRENCY.get(origin.get("country", "MY"), "myr")
    # Guard against duplicate physical-bus schedules on the same route+date+time.
    if body.route_id:
        dupe = await db.schedules.find_one({
            "route_id": body.route_id,
            "departure_date": body.departure_date,
            "departure_time": body.departure_time,
        })
        if dupe:
            raise HTTPException(400,
                f"A schedule already exists for this route on {body.departure_date} at {body.departure_time}. "
                "One route can only have one physical bus per departure slot.")
    doc = body.model_dump()
    doc["currency"] = derived_currency
    doc["id"] = new_id()
    doc["layout_config"] = _layout_for_bus_type(doc.get("bus_type", "Standard"))
    doc["created_at"] = utcnow().isoformat()
    await db.schedules.insert_one(doc)
    doc.pop("_id", None)
    await log_audit(user, "create", "schedule", doc["id"], {
        "from_terminal_id": doc.get("from_terminal_id"),
        "to_terminal_id": doc.get("to_terminal_id"),
        "departure_date": doc.get("departure_date"),
        "departure_time": doc.get("departure_time"),
        "adult_fare": doc.get("adult_fare"),
        "total_seats": doc.get("total_seats"),
        "layout_config": doc.get("layout_config"),
        "currency": derived_currency,
        "route_id": doc.get("route_id"),
    }, request)
    return doc


@api.post("/admin/schedules/bulk")
async def admin_bulk_create_schedules(body: BulkScheduleBody, request: Request, user: dict = Depends(require_admin)):
    """Generate one schedule per date matching days_of_week between start_date and end_date (inclusive).
    Enforces: end_date >= start_date, max 180-day span, no open-ended schedules."""
    try:
        start = datetime.fromisoformat(body.start_date).date()
        end = datetime.fromisoformat(body.end_date).date()
    except ValueError:
        raise HTTPException(400, "Invalid start_date or end_date (expected YYYY-MM-DD)")
    if end < start:
        raise HTTPException(400, "end_date must be on or after start_date")
    span = (end - start).days + 1
    if span > 180:
        raise HTTPException(400, f"Date range too large ({span} days). Max 180 days per bulk create.")
    if not body.days_of_week or any(d < 0 or d > 6 for d in body.days_of_week):
        raise HTTPException(400, "days_of_week must be a non-empty list of 0..6 (0=Mon..6=Sun)")

    origin = await db.terminals.find_one({"id": body.from_terminal_id}, {"_id": 0})
    dest = await db.terminals.find_one({"id": body.to_terminal_id}, {"_id": 0})
    if not origin or not dest:
        raise HTTPException(400, "Invalid from_terminal_id or to_terminal_id")
    if body.from_terminal_id == body.to_terminal_id:
        raise HTTPException(400, "From and To terminals must differ")
    derived_currency = COUNTRY_TO_CURRENCY.get(origin.get("country", "MY"), "myr")

    # Validate optional route link once, up front. When a route is linked,
    # force from = first boarding stop, to = last alighting stop (one physical
    # bus per route+date+time).
    if body.route_id:
        route = await db.routes.find_one({"id": body.route_id}, {"_id": 0})
        if not route:
            raise HTTPException(400, "Linked route_id does not exist")
        boarding = sorted(route.get("boarding_stops", []) or [], key=lambda s: s.get("offset_min", 0))
        alighting = sorted(route.get("alighting_stops", []) or [], key=lambda s: s.get("offset_min", 0))
        if not boarding or not alighting:
            raise HTTPException(400, "Linked route must have at least one boarding and one alighting stop.")
        body.from_terminal_id = boarding[0]["terminal_id"]
        body.to_terminal_id = alighting[-1]["terminal_id"]

    dow_set = set(body.days_of_week)
    created = 0
    skipped = 0
    cur = start
    while cur <= end:
        if cur.weekday() in dow_set:
            # Duplicate detection:
            # - route-linked: enforce ONE physical bus per (route_id, date, time)
            # - unlinked: keep legacy (from + to + date + time) uniqueness
            if body.route_id:
                dupe_filter = {
                    "route_id": body.route_id,
                    "departure_date": cur.isoformat(),
                    "departure_time": body.departure_time,
                }
            else:
                dupe_filter = {
                    "from_terminal_id": body.from_terminal_id,
                    "to_terminal_id": body.to_terminal_id,
                    "departure_date": cur.isoformat(),
                    "departure_time": body.departure_time,
                }
            existing = await db.schedules.find_one(dupe_filter)
            if existing:
                skipped += 1
            else:
                doc = {
                    "id": new_id(),
                    "from_terminal_id": body.from_terminal_id,
                    "to_terminal_id": body.to_terminal_id,
                    "departure_date": cur.isoformat(),
                    "departure_time": body.departure_time,
                    "arrival_time": body.arrival_time,
                    "bus_operator": "Qistna Express",
                    "bus_type": body.bus_type,
                    "adult_fare": body.adult_fare,
                    "child_fare": body.child_fare,
                    "total_seats": body.total_seats,
                    "layout_config": _layout_for_bus_type(body.bus_type),
                    "currency": derived_currency,
                    "created_at": utcnow().isoformat(),
                }
                if body.route_id:
                    doc["route_id"] = body.route_id
                if body.trip_no:
                    doc["trip_no"] = body.trip_no
                await db.schedules.insert_one(doc)
                created += 1
        cur += timedelta(days=1)
    await log_audit(user, "bulk_create", "schedule", None, {
        "from_terminal_id": body.from_terminal_id,
        "to_terminal_id": body.to_terminal_id,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "days_of_week": body.days_of_week,
        "departure_time": body.departure_time,
        "created": created,
        "skipped_duplicates": skipped,
    }, request)
    return {
        "created": created,
        "skipped_duplicates": skipped,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "currency": derived_currency,
    }


@api.delete("/admin/schedules/range")
async def admin_delete_schedules_range(
    from_terminal_id: str,
    to_terminal_id: str,
    start_date: str,
    end_date: str,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Delete all schedules for a route within a date range. Blocks if any bookings exist for those schedules."""
    try:
        datetime.fromisoformat(start_date)
        datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")
    target = await db.schedules.find({
        "from_terminal_id": from_terminal_id,
        "to_terminal_id": to_terminal_id,
        "departure_date": {"$gte": start_date, "$lte": end_date},
    }, {"_id": 0, "id": 1}).to_list(1000)
    ids = [s["id"] for s in target]
    if not ids:
        return {"deleted": 0, "bookings_blocking": 0}
    # Block if any booking references these schedules
    booking_count = await db.bookings.count_documents({"schedule_id": {"$in": ids}})
    if booking_count > 0:
        raise HTTPException(400, f"Cannot delete — {booking_count} booking(s) exist for these schedules")
    result = await db.schedules.delete_many({"id": {"$in": ids}})
    await log_audit(user, "delete_range", "schedule", None, {
        "from_terminal_id": from_terminal_id,
        "to_terminal_id": to_terminal_id,
        "start_date": start_date,
        "end_date": end_date,
        "deleted": result.deleted_count,
    }, request)
    return {"deleted": result.deleted_count, "range": f"{start_date}..{end_date}"}


# ---------- Schedule PATCH + Bulk delete (P0 wiring) ----------
def _build_bulk_delete_query(f: BulkDeleteScheduleFilter) -> dict:
    """Convert a BulkDeleteScheduleFilter into a Mongo query.

    Raises HTTPException(400) if no filter is set at all — this is a safety
    guard so a bad UI form never accidentally deletes all 4,500+ schedules.
    """
    q: dict = {}
    if f.from_terminal_id:
        q["from_terminal_id"] = f.from_terminal_id
    if f.to_terminal_id:
        q["to_terminal_id"] = f.to_terminal_id
    if f.route_id:
        q["route_id"] = f.route_id
    if f.start_date or f.end_date:
        date_q: dict = {}
        if f.start_date:
            try:
                datetime.fromisoformat(f.start_date)
            except ValueError:
                raise HTTPException(400, "Invalid start_date (expected YYYY-MM-DD)")
            date_q["$gte"] = f.start_date
        if f.end_date:
            try:
                datetime.fromisoformat(f.end_date)
            except ValueError:
                raise HTTPException(400, "Invalid end_date (expected YYYY-MM-DD)")
            date_q["$lte"] = f.end_date
        q["departure_date"] = date_q
    if f.unlinked_only:
        # Match schedules that have no route_id at all OR an explicit null.
        q["$or"] = [{"route_id": {"$exists": False}}, {"route_id": None}]
    if not q:
        raise HTTPException(400, "At least one filter must be provided (safety guard).")
    return q


async def _notify_schedule_change(new_sched: dict, old_sched: dict) -> int:
    """Send a schedule-change email to every confirmed booking on this schedule.

    Fire-and-forget per booking so a single email failure doesn't block others.
    Returns the number of confirmed bookings we DISPATCHED emails for (Resend
    itself may still bounce later — we log each result inside email_service).
    """
    from email_service import send_schedule_change  # local import — avoids cycles
    bookings = await db.bookings.find(
        {"schedule_id": new_sched["id"], "status": "confirmed"},
        {"_id": 0},
    ).to_list(500)
    if not bookings:
        return 0
    from_term, to_term = await asyncio.gather(
        db.terminals.find_one({"id": new_sched.get("from_terminal_id")}, {"_id": 0}),
        db.terminals.find_one({"id": new_sched.get("to_terminal_id")}, {"_id": 0}),
    )
    old_departure = f"{old_sched.get('departure_date','')} · {old_sched.get('departure_time','')}"
    new_departure = f"{new_sched.get('departure_date','')} · {new_sched.get('departure_time','')}"
    for b in bookings:
        # Each dispatch is fire-and-forget so one failure doesn't cascade.
        asyncio.create_task(send_schedule_change(
            booking=b, from_term=from_term, to_term=to_term,
            old_departure=old_departure, new_departure=new_departure,
        ))
    return len(bookings)


@api.get("/admin/schedules/{schedule_id}/impact")
async def admin_schedule_impact(schedule_id: str, user: dict = Depends(require_admin)):
    """Return a summary of downstream impact BEFORE the admin edits a schedule.

    Used by the frontend edit modal to warn about changes that will affect
    real customers — number of confirmed bookings, distinct contact emails,
    and whether any of those bookings already have TBS QR passes issued
    (which may need reissuing if the departure time changes).
    """
    sched = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")
    bookings = await db.bookings.find(
        {"schedule_id": schedule_id, "status": {"$in": ["confirmed", "pending_payment"]}},
        {"_id": 0, "reference": 1, "contact_email": 1, "status": 1, "gohub_status": 1,
         "passengers": 1, "gohub_tickets": 1},
    ).to_list(500)
    confirmed = [b for b in bookings if b.get("status") == "confirmed"]
    with_cts_qr = [b for b in confirmed if (b.get("gohub_status") == "confirmed"
                                             and (b.get("gohub_tickets") or []))]
    total_seats = sum(len(b.get("passengers") or []) for b in confirmed)
    emails = sorted({b.get("contact_email") for b in confirmed if b.get("contact_email")})
    return {
        "schedule_id": schedule_id,
        "confirmed_bookings": len(confirmed),
        "pending_bookings": sum(1 for b in bookings if b.get("status") == "pending_payment"),
        "confirmed_passengers": total_seats,
        "bookings_with_cts_qr": len(with_cts_qr),
        "distinct_customer_emails": len(emails),
        "sample_references": [b.get("reference") for b in confirmed[:5]],
    }


@api.patch("/admin/schedules/{schedule_id}")
async def admin_update_schedule(schedule_id: str, body: ScheduleUpdateBody, request: Request,
                                user: dict = Depends(require_admin)):
    existing = await db.schedules.find_one({"id": schedule_id})
    if not existing:
        raise HTTPException(404, "Schedule not found")
    payload = body.model_dump(exclude_unset=True)
    notify = bool(payload.pop("notify_passengers", False))
    # Sentinel handling for `end_date`: empty string = clear the field
    # (via $unset), an actual date = set it, and omission leaves it alone.
    clear_end_date = payload.get("end_date") == ""
    if clear_end_date:
        payload.pop("end_date")
    updates = {k: v for k, v in payload.items() if v is not None}
    if "route_id" in updates:
        route = await db.routes.find_one({"id": updates["route_id"]}, {"_id": 0})
        if not route:
            raise HTTPException(400, "route_id does not exist")
        # Also ensure the schedule's from/to are within the route's boarding/alighting.
        boarding_ids = {s["terminal_id"] for s in route.get("boarding_stops", [])}
        alighting_ids = {s["terminal_id"] for s in route.get("alighting_stops", [])}
        if existing["from_terminal_id"] not in boarding_ids:
            raise HTTPException(400, "This schedule's from_terminal is not part of the target route's boarding stops")
        if existing["to_terminal_id"] not in alighting_ids:
            raise HTTPException(400, "This schedule's to_terminal is not part of the target route's alighting stops")
    if "bus_type" in updates:
        updates["layout_config"] = _layout_for_bus_type(updates["bus_type"])
    if not updates and not clear_end_date:
        raise HTTPException(400, "No changes provided")

    # Detect a customer-facing time/date change BEFORE the update so we can
    # notify only the affected trips (fare/seat changes don't warrant an email).
    time_or_date_changed = any(
        k in updates and updates[k] != existing.get(k)
        for k in ("departure_date", "departure_time")
    )

    mongo_op: dict = {}
    if updates:
        mongo_op["$set"] = updates
    if clear_end_date:
        mongo_op.setdefault("$unset", {})["end_date"] = ""
    result = await db.schedules.update_one({"id": schedule_id}, mongo_op)
    if result.matched_count == 0:
        raise HTTPException(404, "Schedule not found")
    doc = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    await log_audit(
        user, "update", "schedule", schedule_id,
        {"fields": list(updates.keys()) + (["end_date:cleared"] if clear_end_date else []),
         "notify_passengers": notify,
         "time_or_date_changed": time_or_date_changed},
        request,
    )

    notified_count = 0
    if notify and time_or_date_changed:
        # Fire-and-forget: never let email failures roll back a legitimate update.
        notified_count = await _notify_schedule_change(doc, existing)

    return {**doc, "notified_passengers": notified_count}


@api.post("/admin/schedules/bulk-delete/preview")
async def admin_bulk_delete_preview(body: BulkDeleteScheduleFilter, user: dict = Depends(require_admin)):
    """Return a count-only preview so admin can confirm before executing.

    - matched:    total schedules matching the filter
    - blocked:    those with ≥1 booking (cannot be safely deleted)
    - deletable:  matched - blocked
    """
    q = _build_bulk_delete_query(body)
    matched = await db.schedules.count_documents(q)
    if matched == 0:
        return {"matched": 0, "blocked": 0, "deletable": 0}
    # Look up which of those have bookings.
    ids = [s["id"] async for s in db.schedules.find(q, {"_id": 0, "id": 1})]
    blocking_ids = await db.bookings.distinct("schedule_id", {"schedule_id": {"$in": ids}})
    return {
        "matched": matched,
        "blocked": len(blocking_ids),
        "deletable": matched - len(blocking_ids),
    }


@api.post("/admin/schedules/bulk-delete")
async def admin_bulk_delete_execute(body: BulkDeleteScheduleFilter, request: Request,
                                    user: dict = Depends(require_admin)):
    """Delete matching schedules. By default skips any with bookings; set force=true to override."""
    q = _build_bulk_delete_query(body)
    docs = [s async for s in db.schedules.find(q, {"_id": 0, "id": 1})]
    ids = [s["id"] for s in docs]
    if not ids:
        return {"deleted": 0, "skipped": 0}
    blocking_ids = await db.bookings.distinct("schedule_id", {"schedule_id": {"$in": ids}})
    if body.force:
        target_ids = ids
        skipped = 0
    else:
        target_ids = [i for i in ids if i not in blocking_ids]
        skipped = len(blocking_ids)
    if not target_ids:
        return {"deleted": 0, "skipped": skipped, "note": "All matched schedules have bookings. Use force=true to override."}
    result = await db.schedules.delete_many({"id": {"$in": target_ids}})
    # Free any seat_locks tied to the deleted schedules.
    await db.seat_locks.delete_many({"schedule_id": {"$in": target_ids}})
    await log_audit(user, "bulk_delete", "schedule", None, {
        "filter": body.model_dump(exclude_unset=True),
        "deleted": result.deleted_count,
        "skipped_with_bookings": skipped,
        "force": body.force,
    }, request)
    return {"deleted": result.deleted_count, "skipped": skipped, "force": body.force}


# ---------- Seeding & Indexes ----------
MALAYSIAN_TERMINALS = [
    # (city, name, code, state, country)
    ("Kuala Lumpur", "KL Sentral", "KLS", "WP", "MY"),
    ("Kuala Lumpur", "Terminal Bersepadu Selatan (TBS)", "TBS", "WP", "MY"),
    ("Kuala Lumpur", "Pudu Sentral", "PDS", "WP", "MY"),
    ("Kuala Lumpur", "Hentian Duta", "HDT", "WP", "MY"),
    ("Penang", "Sungai Nibong", "SGN", "PG", "MY"),
    ("Penang", "Butterworth Penang Sentral", "BPS", "PG", "MY"),
    ("Johor Bahru", "Larkin Sentral", "LKS", "JH", "MY"),
    ("Johor Bahru", "JB Sentral (Causeway)", "JBS", "JH", "MY"),
    ("Melaka", "Melaka Sentral", "MKS", "ML", "MY"),
    ("Ipoh", "Ipoh Amanjaya", "IPA", "PK", "MY"),
    ("Singapore", "Golden Mile Complex", "GMC", "SG", "SG"),
    ("Singapore", "Queen Street Terminal", "QST", "SG", "SG"),
    ("Kota Bharu", "Kota Bharu Terminal", "KTB", "KN", "MY"),
    ("Kuantan", "Terminal Sentral Kuantan", "TSK", "PH", "MY"),
]

COUNTRY_TO_CURRENCY = {"MY": "myr", "SG": "sgd"}


async def _seed_terminals():
    if await db.terminals.count_documents({}) > 0:
        # Backfill country on any existing record missing it
        async for t in db.terminals.find({"country": {"$exists": False}}):
            country = "SG" if t.get("city") == "Singapore" else "MY"
            await db.terminals.update_one({"id": t["id"]}, {"$set": {"country": country}})
        return
    for city, name, code, state, country in MALAYSIAN_TERMINALS:
        await db.terminals.insert_one(
            {"id": new_id(), "city": city, "name": name, "code": code, "state": state, "country": country}
        )
    logger.info("Seeded %d terminals", len(MALAYSIAN_TERMINALS))


OPERATORS = ["Qistna Express"]
BUS_TYPES = ["VIP 27", "Executive", "Standard"]


async def _seed_schedules():
    if await db.schedules.count_documents({}) > 0:
        return
    terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    # Popular routes
    popular_routes = [
        ("Kuala Lumpur", "Penang"),
        ("Kuala Lumpur", "Johor Bahru"),
        ("Kuala Lumpur", "Melaka"),
        ("Kuala Lumpur", "Ipoh"),
        ("Kuala Lumpur", "Singapore"),
        ("Penang", "Kuala Lumpur"),
        ("Johor Bahru", "Kuala Lumpur"),
        ("Singapore", "Kuala Lumpur"),
        ("Melaka", "Singapore"),
        ("Kuala Lumpur", "Kuantan"),
    ]

    def pick_main(city):
        for t in terms:
            if t["city"] == city:
                return t
        return None

    today = datetime.now(timezone.utc).date()
    total = 0
    for dep_city, arr_city in popular_routes:
        from_t = pick_main(dep_city)
        to_t = pick_main(arr_city)
        if not from_t or not to_t:
            continue
        # also generate second terminal option for KL
        alt_from = None
        if dep_city == "Kuala Lumpur":
            for t in terms:
                if t["city"] == "Kuala Lumpur" and t["id"] != from_t["id"]:
                    alt_from = t
                    break

        for day_offset in range(0, 14):
            dep_date = (today + timedelta(days=day_offset)).isoformat()
            times = [("08:00", "12:30", 55.0), ("11:30", "16:00", 49.0), ("14:00", "18:30", 52.0), ("22:00", "02:30", 65.0)]
            for i, (dt, at, fare) in enumerate(times):
                op = OPERATORS[i % len(OPERATORS)]
                bt = BUS_TYPES[i % len(BUS_TYPES)]
                origin_term = alt_from if (i == 1 and alt_from) else from_t
                sched_currency = COUNTRY_TO_CURRENCY.get(origin_term.get("country", "MY"), "myr")
                # For SGD schedules, approximate local pricing (MYR * 0.33 rounded to nearest whole)
                sched_fare = round(fare * 0.33) if sched_currency == "sgd" else fare
                await db.schedules.insert_one(
                    {
                        "id": new_id(),
                        "from_terminal_id": origin_term["id"],
                        "to_terminal_id": to_t["id"],
                        "departure_date": dep_date,
                        "departure_time": dt,
                        "arrival_time": at,
                        "bus_operator": op,
                        "bus_type": bt,
                        "adult_fare": sched_fare,
                        "rows": 10,
                        "total_seats": 40,
                        "currency": sched_currency,
                        "created_at": utcnow().isoformat(),
                    }
                )
                total += 1
    logger.info("Seeded %d schedules", total)


async def _migrate_operator_names():
    """All schedules belong to single operator 'Qistna Express'."""
    result = await db.schedules.update_many(
        {"bus_operator": {"$ne": "Qistna Express"}},
        {"$set": {"bus_operator": "Qistna Express"}},
    )
    if result.modified_count:
        logger.info("Migrated %d schedules to Qistna Express operator", result.modified_count)


async def _diversify_popular_schedules():
    """For each popular city pair, ensure varied departure times over the next 14 days
    so the 'Popular right now' section shows genuinely different countdowns per route
    (not all landing on the same identical seed slot).

    Safe & idempotent: only adds NEW schedule rows where none exist for that specific
    (from, to, date, time) tuple. Existing schedules & bookings are never touched.
    """
    # Route-specific varied departure time sets — each pair gets its own mix of morning,
    # afternoon, evening, and late-night slots. Times are (dep, arr, fare_myr).
    ROUTE_TIMES: dict = {
        ("Kuala Lumpur", "Melaka"):      [("07:15", "09:45", 25), ("10:30", "13:00", 25), ("13:45", "16:15", 25), ("17:20", "19:50", 28), ("19:00", "21:30", 25)],
        ("Kuala Lumpur", "Johor Bahru"): [("06:45", "11:15", 45), ("09:30", "14:00", 45), ("12:15", "16:45", 45), ("15:40", "20:10", 49), ("19:45", "00:15", 52)],
        ("Kuala Lumpur", "Penang"):      [("07:00", "11:30", 49), ("10:15", "14:45", 49), ("13:30", "18:00", 49), ("17:50", "22:20", 52), ("21:15", "01:45", 55)],
        ("Kuala Lumpur", "Singapore"):   [("07:30", "13:00", 55), ("11:00", "16:30", 55), ("15:15", "20:45", 58), ("22:30", "04:00", 65)],
        ("Kuala Lumpur", "Ipoh"):        [("08:20", "10:45", 35), ("12:10", "14:35", 35), ("16:05", "18:30", 35), ("20:40", "23:05", 38)],
        ("Kuala Lumpur", "Kuantan"):     [("08:15", "12:15", 42), ("13:25", "17:25", 42), ("18:35", "22:35", 45)],
        ("Penang", "Johor Bahru"):       [("06:30", "13:30", 75), ("10:00", "17:00", 75), ("20:00", "03:00", 80)],
        ("Penang", "Kuala Lumpur"):      [("07:45", "12:15", 49), ("11:20", "15:50", 49), ("14:55", "19:25", 49), ("18:30", "23:00", 52)],
        ("Singapore", "Kuala Lumpur"):   [("07:00", "12:30", 55), ("11:45", "17:15", 55), ("16:20", "21:50", 58), ("22:00", "03:30", 65)],
        ("Johor Bahru", "Kuala Lumpur"): [("06:50", "11:20", 45), ("10:05", "14:35", 45), ("13:50", "18:20", 45), ("17:25", "21:55", 49), ("20:30", "01:00", 52)],
        ("Melaka", "Singapore"):         [("07:50", "11:50", 55), ("12:30", "16:30", 55), ("18:10", "22:10", 58)],
    }

    # Pre-index terminals by city -> first matching terminal
    all_terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    city_to_term: dict = {}
    for t in all_terms:
        city_to_term.setdefault(t["city"], t)

    today = datetime.now(timezone.utc).date()
    added = 0
    for (from_city, to_city), times in ROUTE_TIMES.items():
        f = city_to_term.get(from_city)
        t = city_to_term.get(to_city)
        if not f or not t:
            continue
        # Derive currency from origin country (same rule as seed)
        currency = COUNTRY_TO_CURRENCY.get(f.get("country"), "myr")
        for day_offset in range(0, 14):
            dep_date = (today + timedelta(days=day_offset)).isoformat()
            for dep_time, arr_time, fare in times:
                fare_val = fare if currency == "myr" else round(fare * 0.33)
                exists = await db.schedules.find_one(
                    {
                        "from_terminal_id": f["id"],
                        "to_terminal_id": t["id"],
                        "departure_date": dep_date,
                        "departure_time": dep_time,
                    },
                    {"_id": 0, "id": 1},
                )
                if exists:
                    continue
                await db.schedules.insert_one(
                    {
                        "id": new_id(),
                        "from_terminal_id": f["id"],
                        "to_terminal_id": t["id"],
                        "departure_date": dep_date,
                        "departure_time": dep_time,
                        "arrival_time": arr_time,
                        "bus_operator": "Qistna Express",
                        "bus_type": "Executive",
                        "adult_fare": fare_val,
                        "rows": 10,
                        "total_seats": 40,
                        "currency": currency,
                        "created_at": utcnow().isoformat(),
                    }
                )
                added += 1
    if added:
        logger.info("Diversified popular-route schedules: +%d rows", added)


async def _migrate_sg_schedules():
    """One-time migration: for schedules where the origin terminal is in Singapore,
    ensure currency is 'sgd' and fares are adjusted from MYR."""
    sg_terminals = await db.terminals.find({"country": "SG"}, {"_id": 0, "id": 1}).to_list(100)
    sg_ids = [t["id"] for t in sg_terminals]
    if not sg_ids:
        return
    wrong = db.schedules.find({"from_terminal_id": {"$in": sg_ids}, "currency": {"$ne": "sgd"}}, {"_id": 0})
    fixed = 0
    async for s in wrong:
        new_fare = round(float(s.get("adult_fare", 0)) * 0.33)
        await db.schedules.update_one(
            {"id": s["id"]},
            {"$set": {"currency": "sgd", "adult_fare": new_fare}},
        )
        fixed += 1
    if fixed:
        logger.info("Migrated %d SG-origin schedules to SGD pricing", fixed)


async def _seed_admin():
    """Idempotent bootstrap admin seed.

    On every startup:
      1. If no admin exists → seed it with BOOTSTRAP_ADMIN_PASSWORD.
      2. If admin exists AND its current password_hash still verifies against
         BOOTSTRAP_ADMIN_PASSWORD → no-op (steady state).
      3. If admin exists BUT the env password no longer matches the stored
         hash → this usually happens after `.env` changes on production.
         By default we log a big WARNING with recovery instructions.
         If `RESET_ADMIN_ON_BOOT=true` is set in env, we force-resync the
         password_hash to the current env value. This lets operators recover
         a locked-out admin with a single .env flip + restart.
    """
    existing = await db.users.find_one({"email": "admin@starqistna.com"})
    reset_flag = os.environ.get("RESET_ADMIN_ON_BOOT", "").strip().lower() in ("1", "true", "yes")

    if existing:
        updates: dict = {}
        unsets: dict = {}
        if existing.get("role") != "super_admin":
            updates["role"] = "super_admin"
        if existing.get("is_active") is None:
            updates["is_active"] = True
        if not existing.get("is_admin"):
            updates["is_admin"] = True
        if existing.get("full_name") == "Star Qistna Admin":
            updates["full_name"] = "Qistna Express Admin"

        stored_hash = existing.get("password_hash") or ""
        env_matches_stored = bool(stored_hash) and verify_password(BOOTSTRAP_ADMIN_PASSWORD, stored_hash)

        if not env_matches_stored:
            if reset_flag:
                updates["password_hash"] = hash_password(BOOTSTRAP_ADMIN_PASSWORD)
                unsets["must_change_password"] = ""
                logger.warning(
                    "RESET_ADMIN_ON_BOOT=true → admin password re-synced to BOOTSTRAP_ADMIN_PASSWORD. "
                    "IMPORTANT: remove RESET_ADMIN_ON_BOOT from .env and restart so this doesn't run again."
                )
            else:
                logger.warning(
                    "\n" + ("=" * 70) + "\n"
                    "  Admin exists but BOOTSTRAP_ADMIN_PASSWORD in .env does NOT match\n"
                    "  the password hash stored in MongoDB. Login with the env value\n"
                    "  will fail.\n\n"
                    "  To recover, choose ONE:\n"
                    "    A) Set RESET_ADMIN_ON_BOOT=true in backend/.env, restart the\n"
                    "       backend, then remove the flag and restart again.\n"
                    "    B) Run: python scripts/reset_admin_password.py\n"
                    + ("=" * 70)
                )

        if updates or unsets:
            op: dict = {}
            if updates:
                op["$set"] = updates
            if unsets:
                op["$unset"] = unsets
            await db.users.update_one({"id": existing["id"]}, op)
        return

    # First-boot seed.
    await db.users.insert_one(
        {
            "id": new_id(),
            "email": "admin@starqistna.com",
            "full_name": "Qistna Express Admin",
            "phone": "+60123456789",
            "password_hash": hash_password(BOOTSTRAP_ADMIN_PASSWORD),
            "is_admin": True,
            "role": "super_admin",
            "is_active": True,
            "must_change_password": True,
            "created_at": utcnow().isoformat(),
        }
    )
    logger.info("Seeded bootstrap super-admin (email: admin@starqistna.com). Change password on first login.")


async def _seed_promos():
    if await db.promo_codes.count_documents({}) > 0:
        return
    samples = [
        {"code": "WELCOME10", "type": "percent", "value": 10, "currency": "myr",
         "max_uses": 1000, "valid_until": None, "active": True,
         "description": "10% off any booking"},
        {"code": "RAYA5", "type": "flat", "value": 5, "currency": "myr",
         "max_uses": None, "valid_until": None, "active": True,
         "description": "RM 5 off any booking"},
    ]
    for s in samples:
        await db.promo_codes.insert_one(
            {**s, "id": new_id(), "used_count": 0, "created_at": utcnow().isoformat()}
        )
    logger.info("Seeded %d promo codes", len(samples))


async def _seed_bus_types():
    """Seed the three default bus classes we've been using since day one.
    Idempotent — skips any name that already exists (admin may have edited seat counts)."""
    defaults = [
        {"name": "VIP 27", "seat_count": 27,
         "description": "Luxury 2+1 layout with massage seats", "image_url": None},
        {"name": "Executive", "seat_count": 40,
         "description": "Reclining 2+2 seats · premium comfort", "image_url": None},
        {"name": "Standard", "seat_count": 40,
         "description": "Standard 2+2 layout", "image_url": None},
    ]
    inserted = 0
    for bt in defaults:
        if await db.bus_types.find_one({"name": bt["name"]}):
            continue
        await db.bus_types.insert_one({
            **bt,
            "id": new_id(),
            "layout": _layout_for_bus_type(bt["name"]),
            "created_at": utcnow().isoformat(),
        })
        inserted += 1
    if inserted:
        logger.info("Seeded %d default bus types", inserted)


async def _ensure_indexes():
    # Prevent double booking at DB level: unique (schedule_id, seat_number) for non-released rows.
    # Partial unique index on locked/booked statuses.
    await db.seat_locks.create_index(
        [("schedule_id", ASCENDING), ("seat_number", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": {"$in": ["locked", "booked"]}},
        name="uniq_schedule_seat_active",
    )
    await db.users.create_index([("email", ASCENDING)], unique=True)
    await db.bookings.create_index([("user_id", ASCENDING)])
    await db.bookings.create_index([("reference", ASCENDING)], unique=True)
    await db.bookings.create_index([("created_at", -1)])
    await db.bookings.create_index([("status", ASCENDING)])
    await db.terminals.create_index([("city", ASCENDING)])
    await db.schedules.create_index(
        [("from_terminal_id", ASCENDING), ("to_terminal_id", ASCENDING),
         ("departure_date", ASCENDING), ("departure_time", ASCENDING)]
    )
    # One physical bus per route+date+time: enforce uniqueness for route-linked schedules.
    await db.schedules.create_index(
        [("route_id", ASCENDING), ("departure_date", ASCENDING), ("departure_time", ASCENDING)],
        unique=True,
        partialFilterExpression={"route_id": {"$type": "string"}},
        name="uniq_route_departure",
    )
    await db.payment_transactions.create_index([("session_id", ASCENDING)], unique=True)
    await db.promo_codes.create_index([("code", ASCENDING)], unique=True)
    await db.audit_logs.create_index([("created_at", ASCENDING)])
    await db.audit_logs.create_index([("resource", ASCENDING), ("action", ASCENDING)])
    # Auto-expire login attempt records after the rate-limit window elapses
    await db.login_attempts.create_index("failed_at", expireAfterSeconds=LOGIN_WINDOW_SECONDS)
    await db.login_attempts.create_index([("identifier", ASCENDING)])
    await db.login_attempts.create_index([("ip", ASCENDING)])
    # Generic auth throttle (register / 2FA). TTL driven by per-doc `expires_at`.
    await db.auth_throttle.create_index("expires_at", expireAfterSeconds=0)
    await db.auth_throttle.create_index([("ip", ASCENDING), ("scope", ASCENDING)])
    # Password reset tokens: auto-expire at `expires_at` (per-doc TTL)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.password_reset_tokens.create_index([("user_id", ASCENDING)])
    await db.password_reset_tokens.create_index([("token_fp", ASCENDING)])
    # GoHub / CTS audit trail — indexes for post-mortem queries.
    await db.gohub_logs.create_index([("started_at", ASCENDING)])
    await db.gohub_logs.create_index([("operation", ASCENDING)])
    # Admin invites: auto-expire at `expires_at`
    await db.admin_invites.create_index("expires_at", expireAfterSeconds=0)
    await db.admin_invites.create_index([("email", ASCENDING)])
    await db.admin_invites.create_index([("token_fp", ASCENDING)])
    await db.feedback.create_index([("created_at", ASCENDING)])
    await db.feedback.create_index([("status", ASCENDING)])
    # Backfill child_fare on legacy schedules (defaults to half adult fare)
    try:
        await db.schedules.update_many(
            {"child_fare": {"$exists": False}},
            [{"$set": {"child_fare": {"$round": [{"$multiply": ["$adult_fare", 0.5]}, 2]}}}],
        )
    except Exception as e:
        logger.warning("child_fare backfill skipped: %s", e)


# ---------- GoHub / CTS admin probe (dev + smoke-test only) ----------
@api.post("/admin/bookings/{booking_id}/gohub/retry")
async def admin_gohub_retry(booking_id: str, user: dict = Depends(require_admin)):
    """Manually retry CTS ticket issuance for a booking whose earlier attempt
    was skipped or failed (typical use: TBS finished uploading a rate)."""
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking.get("payment_status") != "paid":
        raise HTTPException(409, "Booking is not paid — nothing to issue")
    await _issue_gohub_tickets(booking_id)
    fresh = await db.bookings.find_one(
        {"id": booking_id},
        {"_id": 0, "gohub_status": 1, "gohub_tickets": 1,
         "gohub_error": 1, "gohub_processed_at": 1, "reference": 1},
    )
    return fresh or {"gohub_status": "unknown"}


class GoHubProbeBody(BaseModel):
    trip_no: str = "SQ001"
    boarding_date: Optional[str] = None   # YYYYMMDD; defaults to today MY
    boarding_time: str = "2330"
    seat: str = "1A"
    seat_type: Literal["A", "C", "S", "O"] = "A"
    from_counter: str = "TBS01"
    to_counter: str = "GMC01"
    ic_no: str = ""
    contact: str = ""
    confirm: bool = False
    cancel: bool = False


@api.post("/admin/gohub/probe")
async def admin_gohub_probe(body: GoHubProbeBody, request: Request, user: dict = Depends(require_admin)):
    """Fire a single CTS reserve (optionally confirm/query/cancel) round-trip.

    Meant for post-deploy smoke checks and TBS diagnostics — captures every
    step so an operator can share the result back to TBS support if a call
    fails. Persisted to `gohub_logs` like any other CTS call.
    """
    from gohub_client import GoHubClient, GoHubError, make_opetickno  # local import — keep server startup light
    import uuid as _uuid

    my_today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    trans_id = f"PROBE-{_uuid.uuid4().hex[:12].upper()}"
    client = GoHubClient(db=db, timeout=20)
    steps: list[dict] = []

    # One probe = one seat + one passenger. Compose a spec-compliant opetickno
    # from the trans_id (which acts as our synthetic "booking ref" here).
    probe_ref = trans_id.split("-", 1)[1][:8]
    opetickno = make_opetickno(probe_ref, body.seat)
    probe_seat = {
        "opetickno": opetickno,
        "seatno": body.seat,
        "seattype": body.seat_type,
        "sprice": 55.0,
        "name": "Test Passenger",
        "ic": body.ic_no,
        "contact": body.contact,
    }

    # 1) reserve
    try:
        reserve = await client.reserve_qr(
            trans_id=trans_id, trip_no=body.trip_no,
            trip_date=body.boarding_date or my_today,
            depart_date=body.boarding_date or my_today,
            depart_time=body.boarding_time,
            from_counter=body.from_counter, to_counter=body.to_counter,
            seats=[probe_seat],
        )
        steps.append({"step": "reserve", "ok": True, "data": reserve})
    except GoHubError as e:
        steps.append({"step": "reserve", "ok": False, "error": {"code": e.code, "message": e.message}})
        await log_audit(user, "probe", "gohub", trans_id, {"steps": steps}, request)
        return {"trans_id": trans_id, "steps": steps}

    reserved_id = reserve.get("reservedid") or reserve.get("ReservedID")
    if body.confirm and reserved_id:
        try:
            confirm = await client.confirm_qr(
                reserved_id=reserved_id,
                seats=[{"opetickno": opetickno, "newopetickno": opetickno}],
            )
            steps.append({"step": "confirm", "ok": True, "data": confirm})
        except GoHubError as e:
            steps.append({"step": "confirm", "ok": False, "error": {"code": e.code, "message": e.message}})

    # Query is always by trans_id (not opetickno)
    try:
        q = await client.query_qr(trans_id=trans_id)
        steps.append({"step": "query", "ok": True, "data": q})
    except GoHubError as e:
        steps.append({"step": "query", "ok": False, "error": {"code": e.code, "message": e.message}})

    if body.cancel:
        try:
            c = await client.cancel_qr(trans_id=trans_id, opeticknos=[opetickno])
            steps.append({"step": "cancel", "ok": True, "data": c})
        except GoHubError as e:
            steps.append({"step": "cancel", "ok": False, "error": {"code": e.code, "message": e.message}})

    await log_audit(user, "probe", "gohub", trans_id, {"steps_count": len(steps)}, request)

    # Extract the resulting QR string (from confirm if run, else from reserve) and
    # encode it as a PNG data URL so the demo page can render it directly.
    qr_string = None
    for step in reversed(steps):
        if step["ok"] and step["data"].get("qr"):
            qr_string = step["data"]["qr"]
            break
    qr_png_data_url = None
    if qr_string:
        try:
            import qrcode, base64, io
            img = qrcode.make(qr_string)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_png_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            logger.warning("QR image render failed: %s", e)

    return {
        "trans_id": trans_id,
        "steps": steps,
        "qr_string": qr_string,
        "qr_png_data_url": qr_png_data_url,
    }


@api.get("/")
async def root():
    return {"service": "Transit E1 Bus Booking API", "status": "ok"}


@api.get("/health")
async def health():
    return {"status": "ok", "time": utcnow().isoformat()}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    # We authenticate with Bearer JWTs (not cookies), so allow_credentials=False
    # keeps us safe with allow_origins='*' for dev. In production, lock
    # CORS_ORIGINS to your domain(s) and you can flip credentials on if needed.
    allow_credentials=False,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_start():
    await _ensure_indexes()
    await _seed_terminals()
    await _seed_admin()
    await _seed_schedules()
    await _migrate_sg_schedules()
    await _migrate_operator_names()
    await _diversify_popular_schedules()
    await _seed_promos()
    await _seed_bus_types()
    # Safety net for Stripe webhook drop-offs / async payment settle delays.
    asyncio.create_task(_reconcile_loop())
    logger.info("Qistna Express startup complete")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
