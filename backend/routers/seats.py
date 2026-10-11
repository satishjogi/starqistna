"""Seat lock endpoints."""
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import db
from core.security import current_user
from core.utils import new_id, utcnow

router = APIRouter()

class SeatLockBody(BaseModel):
    schedule_id: str
    seat_numbers: List[str]
@router.post("/seats/lock")
async def lock_seats(body: SeatLockBody, user: Optional[dict] = Depends(current_user)):
    """Attempts to lock given seats for 10 minutes using unique index. Returns failed seats if any."""
    sched = await db.schedules.find_one({"id": body.schedule_id})
    if not sched:
        raise HTTPException(404, "Schedule not found")

    # bus_instance_id groups sibling schedules (same physical bus). Legacy /
    # unlinked schedules fall back to their own id so behaviour is unchanged.
    bus_instance_id = sched.get("bus_instance_id") or sched["id"]

    lock_token = new_id()
    expires_at = utcnow() + timedelta(minutes=10)
    owner = user["id"] if user else f"guest:{lock_token}"

    acquired = []
    failed = []
    for sn in body.seat_numbers:
        # Try to insert; unique index on (bus_instance_id, seat_number) for
        # non-released seats prevents duplicates across sibling schedules too.
        # Strategy: delete any expired locks for this seat first, then insert.
        await db.seat_locks.delete_many(
            {
                "bus_instance_id": bus_instance_id,
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
                    "bus_instance_id": bus_instance_id,
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
@router.post("/seats/release")
async def release_seats(lock_token: str):
    await db.seat_locks.delete_many({"lock_token": lock_token, "status": "locked"})
    return {"released": True}
