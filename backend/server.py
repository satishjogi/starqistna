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
import bcrypt
import jwt
import pyotp
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
from email_service import send_booking_confirmation

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

app = FastAPI(title="Transit E1 - Bus Booking API")
api = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("transit")


# ---------- Helpers ----------
def utcnow():
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


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
    return user


async def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
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


# ---------- Models ----------
class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
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
    query = {}
    if q:
        query = {
            "$or": [
                {"city": {"$regex": q, "$options": "i"}},
                {"name": {"$regex": q, "$options": "i"}},
                {"code": {"$regex": q, "$options": "i"}},
            ]
        }
    terminals = await db.terminals.find(query, {"_id": 0}).sort("city", 1).to_list(500)
    # Group by city for UI
    by_city: dict = {}
    for t in terminals:
        by_city.setdefault(t["city"], []).append(t)
    grouped = [{"city": city, "terminals": sorted(items, key=lambda x: x["name"])} for city, items in sorted(by_city.items())]
    return {"grouped": grouped, "all": terminals}


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

    # Add booked seat counts
    for s in schedules:
        booked_count = await db.seat_locks.count_documents(
            {"schedule_id": s["id"], "status": {"$in": ["locked", "booked"]}}
        )
        s["seats_available"] = s["total_seats"] - booked_count

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
    enrich with terminals + seats-available + pricing.
    """
    now = datetime.now(timezone.utc)
    today_iso = now.date().isoformat()
    current_hhmm = now.strftime("%H:%M")

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

        # How many minutes until departure (positive only)
        try:
            dep_dt = datetime.fromisoformat(
                f"{sched['departure_date']}T{sched['departure_time']}:00+00:00"
            )
            mins_until = max(0, int((dep_dt - now).total_seconds() // 60))
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
    return {"generated_at": now.isoformat(), "items": suggestions[: max(1, min(limit, 12))]}


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

    # Build seat layout: 2 columns + aisle + 2 columns, N rows
    rows = sched.get("rows", 10)
    layout = []
    seat_idx = 0
    for r in range(1, rows + 1):
        row_seats = []
        for col in ["A", "B", "_", "C", "D"]:
            if col == "_":
                row_seats.append({"seat_number": None, "aisle": True})
            else:
                sn = f"{r}{col}"
                seat_idx += 1
                row_seats.append(
                    {
                        "seat_number": sn,
                        "status": booked_seats.get(sn, "available"),
                    }
                )
        layout.append(row_seats)

    return {"schedule": sched, "from": from_term, "to": to_term, "layout": layout}


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


@api.post("/auth/login")
async def login(body: LoginBody, request: Request):
    ip = _client_ip(request)
    email = body.email.lower()
    await _check_login_rate_limit(ip, email)

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await _record_login_failure(ip, email)
        raise HTTPException(401, "Invalid email or password")

    # If 2FA is enabled, return a short-lived challenge instead of the JWT.
    # We clear password-attempt counters here because the password was correct;
    # the 2FA step has its own brute-force protection via the 5-minute challenge expiry.
    await _clear_login_attempts(ip, email)

    if user.get("totp_enabled"):
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
    user_public = {k: v for k, v in user.items() if k not in ("password_hash", "_id", "totp_secret")}
    return TokenResponse(access_token=issue_jwt(user["id"], user["email"]), user=user_public)


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
    child_fare = round(adult_fare * 0.5, 2)
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
    for b in items:
        b["from"] = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
        b["to"] = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
    return items


@api.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, user: Optional[dict] = Depends(current_user)):
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")
    # allow: owner, admin, or guest who knows the id+email (for confirmation)
    b["from"] = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
    b["to"] = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
    b["schedule"] = await db.schedules.find_one({"id": b["schedule_id"]}, {"_id": 0})
    return b


# ---------- Payments (Stripe) ----------
def _payment_options_for(currency: str) -> list:
    """Return list of payment method options based on currency.
    All methods are processed through Stripe using the same merchant account.
    Option `id` maps directly to Stripe `payment_method_types`.
    - Card: universal (Visa/Mastercard/Amex) — all currencies
    - GrabPay: MY + SG markets (MYR, SGD)
    - FPX: MY online banking (MYR only)
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
async def payment_options(booking_id: str):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
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

    currency = booking["pricing"].get("currency", "myr")
    available_ids = {o["id"] for o in _payment_options_for(currency) if o["available"]}
    if body.gateway not in available_ids:
        raise HTTPException(400, f"Payment method '{body.gateway}' is not available for {currency.upper()} bookings")

    # SERVER-SIDE amount (never trust frontend)
    amount = float(booking["pricing"]["total"])

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)

    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&booking_id={body.booking_id}"
    cancel_url = f"{origin}/payment/cancel?booking_id={body.booking_id}"

    checkout_req = CheckoutSessionRequest(
        amount=amount,
        currency=currency,
        success_url=success_url,
        cancel_url=cancel_url,
        payment_methods=[body.gateway],  # card | grabpay | fpx
        metadata={
            "booking_id": body.booking_id,
            "booking_reference": booking["reference"],
            "user_id": booking.get("user_id") or "guest",
            "payment_method": body.gateway,
        },
    )
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)

    await db.payment_transactions.insert_one(
        {
            "id": new_id(),
            "session_id": session.session_id,
            "booking_id": body.booking_id,
            "amount": amount,
            "currency": currency,
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
        from_term = await db.terminals.find_one({"id": booking.get("from_terminal_id")}, {"_id": 0})
        to_term = await db.terminals.find_one({"id": booking.get("to_terminal_id")}, {"_id": 0})
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
    return {
        "users": await db.users.count_documents({}),
        "bookings": await db.bookings.count_documents({}),
        "confirmed_bookings": await db.bookings.count_documents({"status": "confirmed"}),
        "terminals": await db.terminals.count_documents({}),
        "schedules": await db.schedules.count_documents({}),
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
        query["actor_email"] = {"$regex": actor_email, "$options": "i"}
    limit = max(1, min(limit, 1000))
    items = await db.audit_logs.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"total": len(items), "items": items}


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
    rows: int = 10
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
    rows: int = 10


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
    doc["total_seats"] = doc["rows"] * 4
    doc["created_at"] = utcnow().isoformat()
    await db.schedules.insert_one(doc)
    doc.pop("_id", None)
    await log_audit(user, "create", "schedule", doc["id"], {
        "from_terminal_id": doc.get("from_terminal_id"),
        "to_terminal_id": doc.get("to_terminal_id"),
        "departure_date": doc.get("departure_date"),
        "departure_time": doc.get("departure_time"),
        "adult_fare": doc.get("adult_fare"),
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
                    "rows": body.rows,
                    "total_seats": body.rows * 4,
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
    if await db.users.find_one({"email": "admin@starqistna.com"}):
        return
    await db.users.insert_one(
        {
            "id": new_id(),
            "email": "admin@starqistna.com",
            "full_name": "Star Qistna Admin",
            "phone": "+60123456789",
            "password_hash": hash_password("Admin@123"),
            "is_admin": True,
            "created_at": utcnow().isoformat(),
        }
    )
    logger.info("Seeded admin user admin@starqistna.com / Admin@123")


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
    await db.terminals.create_index([("city", ASCENDING)])
    await db.schedules.create_index(
        [("from_terminal_id", ASCENDING), ("to_terminal_id", ASCENDING), ("departure_date", ASCENDING)]
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


@api.get("/")
async def root():
    return {"service": "Transit E1 Bus Booking API", "status": "ok"}


@api.get("/health")
async def health():
    return {"status": "ok", "time": utcnow().isoformat()}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
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
