"""Admin route template CRUD endpoints."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.audit import log_audit
from core.config import db
from core.security import require_admin
from core.utils import new_id, utcnow

router = APIRouter()

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


@router.get("/admin/routes")
async def admin_list_routes(user: dict = Depends(require_admin)):
    items = await db.routes.find({}, {"_id": 0}).sort("code", 1).to_list(500)
    return items


@router.get("/admin/routes/{route_id}")
async def admin_get_route(route_id: str, user: dict = Depends(require_admin)):
    doc = await db.routes.find_one({"id": route_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Route not found")
    return doc


@router.post("/admin/routes")
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


@router.patch("/admin/routes/{route_id}")
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


@router.delete("/admin/routes/{route_id}")
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
