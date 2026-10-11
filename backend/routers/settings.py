"""Public and admin application settings endpoints."""
from cachetools import TTLCache
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.audit import log_audit
from core.config import db
from core.security import require_admin
from core.utils import utcnow
from services.settings import DEFAULT_SGD_TO_MYR, _get_settings

router = APIRouter()
_settings_cache: TTLCache = TTLCache(maxsize=2, ttl=60)

DEFAULT_SGD_TO_MYR = 3.50


class AppSettingsBody(BaseModel):
    sgd_to_myr_rate: float = Field(gt=0, le=20)


@router.get("/settings")
async def public_settings():
    """Public — used by the frontend to compute the bracketed MYR approx for SGD trips."""
    if "global" in _settings_cache:
        return _settings_cache["global"]
    s = await _get_settings()
    payload = {"sgd_to_myr_rate": s.get("sgd_to_myr_rate", DEFAULT_SGD_TO_MYR)}
    _settings_cache["global"] = payload
    return payload


@router.patch("/admin/settings")
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
