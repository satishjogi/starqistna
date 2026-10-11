"""Public and admin bus type catalog endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.audit import log_audit
from core.config import db
from core.security import require_admin
from core.utils import _layout_for_bus_type, new_id, utcnow

router = APIRouter()

class CreateBusTypeBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    seat_count: int = Field(ge=12, le=60)
    image_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=200)
class UpdateBusTypeBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    seat_count: Optional[int] = Field(default=None, ge=12, le=60)
    image_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=200)
@router.get("/bus-types")
async def list_bus_types_public():
    """Public read — used by the schedule form so admins can pick a bus.
    Ordered by name for predictable dropdown listing."""
    items = await db.bus_types.find({}, {"_id": 0}).sort("name", 1).to_list(200)
    return items
@router.get("/admin/bus-types")
async def admin_list_bus_types(_: dict = Depends(require_admin)):
    items = await db.bus_types.find({}, {"_id": 0}).sort("name", 1).to_list(200)
    return items
@router.post("/admin/bus-types")
async def admin_create_bus_type(body: CreateBusTypeBody, request: Request, user: dict = Depends(require_admin)):
    name = body.name.strip()
    if await db.bus_types.find_one({"name": name}):
        raise HTTPException(400, "A bus type with this name already exists.")
    doc = {
        "id": new_id(),
        "name": name,
        "seat_count": body.seat_count,
        "image_url": (body.image_url or "").strip() or None,
        "description": (body.description or "").strip() or None,
        "layout": _layout_for_bus_type(name),
        "created_at": utcnow().isoformat(),
    }
    await db.bus_types.insert_one(doc)
    await log_audit(user, "create", "bus_type", doc["id"],
                    {"name": name, "seat_count": body.seat_count}, request)
    return {k: v for k, v in doc.items() if k != "_id"}
@router.patch("/admin/bus-types/{bus_type_id}")
async def admin_update_bus_type(bus_type_id: str, body: UpdateBusTypeBody, request: Request, user: dict = Depends(require_admin)):
    updates: dict = {}
    if body.name is not None:
        new_name = body.name.strip()
        clash = await db.bus_types.find_one({"name": new_name, "id": {"$ne": bus_type_id}})
        if clash:
            raise HTTPException(400, "Another bus type already uses this name.")
        updates["name"] = new_name
        updates["layout"] = _layout_for_bus_type(new_name)
    if body.seat_count is not None:
        updates["seat_count"] = body.seat_count
    if body.image_url is not None:
        updates["image_url"] = body.image_url.strip() or None
    if body.description is not None:
        updates["description"] = body.description.strip() or None
    if not updates:
        raise HTTPException(400, "No fields to update.")
    result = await db.bus_types.update_one({"id": bus_type_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Bus type not found")
    doc = await db.bus_types.find_one({"id": bus_type_id}, {"_id": 0})
    await log_audit(user, "update", "bus_type", bus_type_id,
                    {"fields": list(updates.keys())}, request)
    return doc
@router.delete("/admin/bus-types/{bus_type_id}")
async def admin_delete_bus_type(bus_type_id: str, request: Request, user: dict = Depends(require_admin)):
    existing = await db.bus_types.find_one({"id": bus_type_id}, {"_id": 0, "name": 1})
    if not existing:
        raise HTTPException(404, "Bus type not found")
    # Block deletion if any schedule still references this bus type by name.
    in_use = await db.schedules.count_documents({"bus_type": existing["name"]})
    if in_use > 0:
        raise HTTPException(400, f"Cannot delete — {in_use} schedule(s) still use '{existing['name']}'.")
    await db.bus_types.delete_one({"id": bus_type_id})
    await log_audit(user, "delete", "bus_type", bus_type_id,
                    {"name": existing["name"]}, request)
    return {"deleted": True}
