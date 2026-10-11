"""Admin user management (invites, roles, activation)."""
import os
import secrets
from datetime import timedelta

import bcrypt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from core.audit import log_audit
from core.config import db, logger
from core.security import _check_auth_throttle, _client_ip, _fingerprint, _record_auth_failure, _validate_password_strength, hash_password, require_admin, require_super_admin
from core.utils import new_id, utcnow
from email_service import send_admin_invite

router = APIRouter()



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


@router.get("/admin/admins")
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


@router.post("/admin/admins/invite")
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


@router.post("/admin/admins/accept-invite")
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


@router.patch("/admin/admins/{admin_id}/role")
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


@router.patch("/admin/admins/{admin_id}/active")
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


@router.delete("/admin/admins/{admin_id}")
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


@router.delete("/admin/admins/invites/{invite_id}")
async def cancel_admin_invite(invite_id: str, request: Request, user: dict = Depends(require_super_admin)):
    result = await db.admin_invites.update_one(
        {"id": invite_id, "accepted_at": None, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Invite not found or already handled")
    await log_audit(user, "cancel_invite", "admin", invite_id, {}, request)
    return {"ok": True}
