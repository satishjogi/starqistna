"""
Transit E1 - Bus Booking System Backend
FastAPI + MongoDB. API-first so future mobile (React Native) uses same endpoints.
"""
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, status
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
from typing import List, Optional, Literal
from datetime import datetime, timezone, timedelta
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionResponse,
    CheckoutStatusResponse,
    CheckoutSessionRequest,
)
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
    from_terminal_id: str,
    to_terminal_id: str,
    date: str,  # YYYY-MM-DD
):
    # Find schedules matching date or recurring (we store schedules with `departure_date` field)
    query = {
        "from_terminal_id": from_terminal_id,
        "to_terminal_id": to_terminal_id,
        "departure_date": date,
    }

    # If searching for "today" in local Malaysia/Singapore time (UTC+8 — same offset for both),
    # hide buses whose departure time has already passed so users don't book a departed trip.
    local_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(local_tz)
    today_local_iso = now_local.date().isoformat()
    if date == today_local_iso:
        query["departure_time"] = {"$gte": now_local.strftime("%H:%M")}

    schedules = await db.schedules.find(query, {"_id": 0}).sort("departure_time", 1).to_list(200)

    # Enrich with terminal names
    from_term = await db.terminals.find_one({"id": from_terminal_id}, {"_id": 0})
    to_term = await db.terminals.find_one({"id": to_terminal_id}, {"_id": 0})

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
        "from": from_term,
        "to": to_term,
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
                "operator": sched.get("bus_operator", "Star Qistna"),
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
    if user and user.get("password_hash"):
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
    issuer = "Star Qistna"
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
    return user


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


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


# ---------- Google Social Login (Emergent-managed) ----------
# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
class GoogleSessionBody(BaseModel):
    session_id: str


@api.post("/auth/google/session")
async def google_session(body: GoogleSessionBody):
    """Exchange an Emergent OAuth session_id for our own JWT (auto-linking by email).
    If the existing account has 2FA enabled, returns a 2FA challenge token instead.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": body.session_id},
            )
    except httpx.HTTPError as e:
        logging.exception("Emergent auth call failed")
        raise HTTPException(502, f"Auth provider unavailable: {e}")

    if resp.status_code != 200:
        raise HTTPException(401, "Invalid or expired Google session")

    data = resp.json()
    email = (data.get("email") or "").lower().strip()
    name = data.get("name") or ""
    picture = data.get("picture") or ""
    if not email:
        raise HTTPException(400, "Google account did not return an email")

    # Auto-link: find existing user by email, else create
    existing = await db.users.find_one({"email": email})
    if existing:
        user_id = existing["id"]
        updates = {"google_linked": True, "last_login_at": utcnow().isoformat()}
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
            "password_hash": "",  # no password for google-only accounts
            "is_admin": False,
            "google_linked": True,
            "picture": picture,
            "created_at": utcnow().isoformat(),
            "last_login_at": utcnow().isoformat(),
        }
        await db.users.insert_one(user_doc)

    # Enforce 2FA if enabled on the account
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

    pricing_promo = None
    if body.promo_code:
        pricing_promo = await _find_valid_promo(body.promo_code, sched.get("currency", "myr"))
    pricing = _calc_pricing(sched, [p.model_dump() for p in body.passengers], pricing_promo)

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
        "from_terminal_id": sched["from_terminal_id"],
        "to_terminal_id": sched["to_terminal_id"],
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
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)

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


async def _finalize_booking(booking_id: str, session_id: str):
    """Idempotent: mark seats booked, confirm booking, increment promo use, mark txn finalized, send email."""
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
    # Fire-and-forget email delivery
    if booking:
        from_term, to_term = await asyncio.gather(
            db.terminals.find_one({"id": booking.get("from_terminal_id")}, {"_id": 0}),
            db.terminals.find_one({"id": booking.get("to_terminal_id")}, {"_id": 0}),
        )
        asyncio.create_task(send_booking_confirmation(booking, from_term, to_term))


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body_bytes = await request.body()
    sig = request.headers.get("Stripe-Signature")
    host_url = str(request.base_url).rstrip("/")

    # In production, STRIPE_WEBHOOK_SECRET must be set so the library verifies the
    # Stripe-Signature header via stripe.Webhook.construct_event(). If it is not
    # set (e.g. local/dev), we fall back to unverified parsing but log a warning.
    if not STRIPE_WEBHOOK_SECRET:
        logger.warning(
            "stripe_webhook: STRIPE_WEBHOOK_SECRET is not set — signature verification is disabled. "
            "Set STRIPE_WEBHOOK_SECRET in backend/.env for production."
        )
    if STRIPE_WEBHOOK_SECRET and not sig:
        # Signature header missing but we expect to verify — reject.
        raise HTTPException(400, "Missing Stripe-Signature header")

    stripe_checkout = StripeCheckout(
        api_key=STRIPE_API_KEY,
        webhook_secret=STRIPE_WEBHOOK_SECRET or None,
        webhook_url=f"{host_url}/api/webhook/stripe",
    )
    try:
        event = await stripe_checkout.handle_webhook(body_bytes, sig)
    except Exception as e:
        logger.exception("Webhook error: %s", e)
        raise HTTPException(400, "Invalid webhook")

    if event.payment_status == "paid" and event.session_id:
        txn = await db.payment_transactions.find_one({"session_id": event.session_id})
        if txn and not txn.get("booking_finalized"):
            await _finalize_booking(txn["booking_id"], event.session_id)
    return {"ok": True}


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
    bus_operator: str = "Star Qistna"
    bus_type: Literal["VIP 27", "Executive", "Standard"] = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)
    currency: str = "myr"


class BulkScheduleBody(BaseModel):
    from_terminal_id: str
    to_terminal_id: str
    start_date: str  # YYYY-MM-DD  (first trip)
    end_date: str    # YYYY-MM-DD  (last trip — inclusive)
    days_of_week: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon..6=Sun
    departure_time: str
    arrival_time: str
    bus_type: Literal["VIP 27", "Executive", "Standard"] = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)


# (AppSettingsBody is defined alongside the settings endpoints earlier in the file.)


class TerminalCreateBody(BaseModel):
    city: str = Field(min_length=1)
    name: str = Field(min_length=1)
    code: str = Field(min_length=2, max_length=6)
    state: Optional[str] = None
    country: Literal["MY", "SG"] = "MY"


class TerminalUpdateBody(BaseModel):
    city: Optional[str] = None
    name: Optional[str] = None
    code: Optional[str] = None
    state: Optional[str] = None
    country: Optional[Literal["MY", "SG"]] = None


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


@api.post("/admin/schedules")
async def admin_create_schedule(body: CreateScheduleBody, request: Request, user: dict = Depends(require_admin)):
    # Auto-derive currency from origin terminal country (SG → SGD, else MYR)
    origin = await db.terminals.find_one({"id": body.from_terminal_id}, {"_id": 0})
    if not origin:
        raise HTTPException(400, "Invalid from_terminal_id")
    derived_currency = COUNTRY_TO_CURRENCY.get(origin.get("country", "MY"), "myr")
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

    dow_set = set(body.days_of_week)
    created = 0
    skipped = 0
    cur = start
    while cur <= end:
        if cur.weekday() in dow_set:
            # Avoid exact duplicates (same route + date + time)
            existing = await db.schedules.find_one({
                "from_terminal_id": body.from_terminal_id,
                "to_terminal_id": body.to_terminal_id,
                "departure_date": cur.isoformat(),
                "departure_time": body.departure_time,
            })
            if existing:
                skipped += 1
            else:
                await db.schedules.insert_one({
                    "id": new_id(),
                    "from_terminal_id": body.from_terminal_id,
                    "to_terminal_id": body.to_terminal_id,
                    "departure_date": cur.isoformat(),
                    "departure_time": body.departure_time,
                    "arrival_time": body.arrival_time,
                    "bus_operator": "Star Qistna",
                    "bus_type": body.bus_type,
                    "adult_fare": body.adult_fare,
                    "child_fare": body.child_fare,
                    "total_seats": body.total_seats,
                    "layout_config": _layout_for_bus_type(body.bus_type),
                    "currency": derived_currency,
                    "created_at": utcnow().isoformat(),
                })
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


OPERATORS = ["Star Qistna"]
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
    """All schedules belong to single operator 'Star Qistna'."""
    result = await db.schedules.update_many(
        {"bus_operator": {"$ne": "Star Qistna"}},
        {"$set": {"bus_operator": "Star Qistna"}},
    )
    if result.modified_count:
        logger.info("Migrated %d schedules to Star Qistna operator", result.modified_count)


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
                        "bus_operator": "Star Qistna",
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
    existing = await db.users.find_one({"email": "admin@starqistna.com"})
    if existing:
        # Migrate: ensure the bootstrap admin is a super_admin and active.
        updates: dict = {}
        if existing.get("role") != "super_admin":
            updates["role"] = "super_admin"
        if existing.get("is_active") is None:
            updates["is_active"] = True
        if updates:
            await db.users.update_one({"id": existing["id"]}, {"$set": updates})
        return
    await db.users.insert_one(
        {
            "id": new_id(),
            "email": "admin@starqistna.com",
            "full_name": "Star Qistna Admin",
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
    logger.info("Star Qistna startup complete")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
