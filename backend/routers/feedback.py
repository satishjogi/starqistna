"""Customer feedback endpoints (public submit + admin review)."""
import asyncio
import secrets
from typing import Literal, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from core.audit import log_audit
from core.config import JWT_ALG, JWT_SECRET, db, logger
from core.security import _check_auth_throttle, _client_ip, _record_auth_failure, require_admin
from core.utils import new_id, utcnow
from email_service import send_feedback_admin_notification, send_feedback_confirmation

router = APIRouter()



class FeedbackBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    email: EmailStr
    category: Literal["general", "booking_issue", "complaint", "suggestion", "praise"] = "general"
    booking_reference: Optional[str] = Field(default=None, max_length=40)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=10, max_length=4000)


class FeedbackStatusBody(BaseModel):
    status: Literal["new", "in_progress", "resolved"]


# ---------- Customer feedback ----------
FEEDBACK_ADMIN_EMAIL = "admin@starqistna.com"


def _feedback_ref() -> str:
    """Short, human-friendly reference e.g. F-7X2M9K."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars
    return "F-" + "".join(secrets.choice(alphabet) for _ in range(6))


@router.post("/feedback")
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


@router.get("/admin/feedback")
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


@router.patch("/admin/feedback/{feedback_id}")
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
