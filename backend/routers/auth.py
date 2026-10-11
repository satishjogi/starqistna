"""Authentication, account recovery, and Google OAuth endpoints."""
import httpx
import os
import secrets
from datetime import timedelta
from typing import Optional

import bcrypt
import jwt
import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from core.config import JWT_ALG, JWT_SECRET, db, logger
from core.security import (
    _check_auth_throttle,
    _clear_auth_attempts,
    _client_ip,
    _fingerprint,
    _record_auth_failure,
    _validate_password_strength,
    current_user,
    hash_password,
    issue_jwt,
    require_user,
    verify_password,
)
from core.utils import new_id, utcnow
from email_service import send_password_reset

router = APIRouter()

LOGIN_MAX_ATTEMPTS_PER_IDENTIFIER = 5   # same IP + email within window
LOGIN_MAX_ATTEMPTS_PER_IP = 20          # same IP across all emails within window
LOGIN_WINDOW_SECONDS = 15 * 60          # 15 minutes


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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/auth/register", response_model=TokenResponse)
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


@router.post("/auth/login")
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


@router.post("/auth/2fa/verify")
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
RESET_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour


@router.post("/auth/forgot-password")
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


@router.post("/auth/reset-password")
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


@router.post("/auth/2fa/setup")
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


@router.post("/auth/2fa/enable")
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


@router.post("/auth/2fa/disable")
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


@router.get("/auth/me")
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


@router.post("/auth/set-password")
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


@router.post("/auth/change-password")
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
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


class GoogleCallbackBody(BaseModel):
    code: str
    redirect_uri: str


@router.post("/auth/google/callback")
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
