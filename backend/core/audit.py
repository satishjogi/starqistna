"""Admin audit-log helper."""
from typing import Optional

from fastapi import Request

from core.config import db, logger
from core.utils import new_id, utcnow


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
