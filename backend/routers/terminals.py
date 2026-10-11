"""Terminal endpoints (public list + admin CRUD)."""
import re

from cachetools import TTLCache
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import log_audit
from core.config import db
from core.security import require_admin
from core.utils import new_id

router = APIRouter()

_terminals_cache: TTLCache = TTLCache(maxsize=8, ttl=300)   # 5 min


class TerminalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    city: str
    name: str
    code: str
    state: Optional[str] = None


@router.get("/terminals")
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


@router.get("/admin/terminals")
async def admin_list_terminals(user: dict = Depends(require_admin)):
    items = await db.terminals.find({}, {"_id": 0}).sort("city", 1).to_list(500)
    # attach schedule counts for delete safety
    for t in items:
        t["schedule_count"] = await db.schedules.count_documents(
            {"$or": [{"from_terminal_id": t["id"]}, {"to_terminal_id": t["id"]}]}
        )
    return items


@router.post("/admin/terminals")
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


@router.patch("/admin/terminals/{terminal_id}")
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


@router.delete("/admin/terminals/{terminal_id}")
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
