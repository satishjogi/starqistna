"""Shared auth utilities: password hashing, JWT, auth dependencies, throttling,
and password-strength validation. Behavior is identical to the former server.py code."""
import hashlib
from datetime import timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import JWT_ALG, JWT_EXPIRE_HOURS, JWT_SECRET, db, logger
from core.utils import utcnow

bearer = HTTPBearer(auto_error=False)


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


async def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


async def require_super_admin(user: dict = Depends(require_admin)) -> dict:
    """Super-admins manage other admins. Regular admins cannot."""
    if user.get("role") != "super_admin":
        raise HTTPException(403, "Super-admin access required")
    return user


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
