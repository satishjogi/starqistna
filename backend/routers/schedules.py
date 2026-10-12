"""Admin schedule management endpoints."""
import asyncio
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.audit import log_audit
from core.config import db, logger
from core.security import require_admin
from core.utils import _layout_for_bus_type, utcnow

router = APIRouter()

@router.get("/admin/schedules")
async def admin_schedules(user: dict = Depends(require_admin)):
    items = await db.schedules.find({}, {"_id": 0}).sort("departure_date", 1).to_list(500)
    return items


class CreateScheduleBody(BaseModel):
    from_terminal_id: str
    to_terminal_id: str
    departure_date: str
    departure_time: str
    arrival_time: str
    bus_operator: str = "Qistna Express"
    bus_type: str = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)
    currency: str = "myr"
    # Optional link to the Routes system (P0 wiring). If provided, the schedule
    # is treated as an instance of that route — customer flow will use the
    # route's pickup/dropoff pairings for pricing + boarding UX.
    route_id: Optional[str] = None
    trip_no: Optional[str] = None  # CTS/GoHub trip identifier, max 10 chars


class ScheduleUpdateBody(BaseModel):
    departure_date: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    bus_operator: Optional[str] = None
    bus_type: Optional[str] = None
    adult_fare: Optional[float] = None
    child_fare: Optional[float] = None
    total_seats: Optional[int] = Field(default=None, ge=12, le=60)
    route_id: Optional[str] = None
    trip_no: Optional[str] = None
    # Last date this schedule series is valid. Set (or extend) this via the
    # edit modal to retire a trip without deleting rows and losing bookings.
    # Same field that `BulkCreateScheduleBody.end_date` already writes at
    # creation time — reused here so there's exactly one "trip stop date"
    # concept in the system.  Empty string CLEARS a previously-set value.
    end_date: Optional[str] = None
    # When true AND the update changes departure_date or departure_time,
    # send a notification email to every confirmed passenger on this schedule.
    notify_passengers: bool = False


class BulkScheduleBody(BaseModel):
    from_terminal_id: str
    to_terminal_id: str
    start_date: str  # YYYY-MM-DD  (first trip)
    end_date: str    # YYYY-MM-DD  (last trip — inclusive)
    days_of_week: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon..6=Sun
    departure_time: str
    arrival_time: str
    bus_type: str = "Standard"
    adult_fare: float
    child_fare: float
    total_seats: int = Field(ge=12, le=60, default=40)
    # Optional link to a Route (same rationale as CreateScheduleBody).
    route_id: Optional[str] = None
    trip_no: Optional[str] = None


class BulkDeleteScheduleFilter(BaseModel):
    """Filter for bulk delete previews and executions.

    All fields are optional — combine to narrow the match. At least one filter
    MUST be provided or the request is rejected (safety guard).
    """
    from_terminal_id: Optional[str] = None
    to_terminal_id: Optional[str] = None
    start_date: Optional[str] = None  # YYYY-MM-DD (inclusive)
    end_date: Optional[str] = None    # YYYY-MM-DD (inclusive)
    route_id: Optional[str] = None
    unlinked_only: bool = False       # schedules where route_id is null / missing
    force: bool = False               # allow deleting even schedules that have bookings


@router.post("/admin/schedules")
async def admin_create_schedule(body: CreateScheduleBody, request: Request, user: dict = Depends(require_admin)):
    # Validate route link (if any) — from/to must be one of the route's stops.
    if body.route_id:
        route = await db.routes.find_one({"id": body.route_id}, {"_id": 0})
        if not route:
            raise HTTPException(400, "Linked route_id does not exist")
        boarding_ids = {s["terminal_id"] for s in route.get("boarding_stops", [])}
        alighting_ids = {s["terminal_id"] for s in route.get("alighting_stops", [])}
        if body.from_terminal_id not in boarding_ids:
            raise HTTPException(400, "from_terminal_id must be one of the route's boarding stops")
        if body.to_terminal_id not in alighting_ids:
            raise HTTPException(400, "to_terminal_id must be one of the route's alighting stops")
    # Auto-derive currency from origin terminal country (SG → SGD, else MYR)
    origin = await db.terminals.find_one({"id": body.from_terminal_id}, {"_id": 0})
    if not origin:
        raise HTTPException(400, "Invalid from_terminal_id")
    derived_currency = COUNTRY_TO_CURRENCY.get(origin.get("country", "MY"), "myr")
    # Resolve bus_instance_id — same physical bus per (route_id, date, time).
    # Route-linked schedules SHARE the seat pool with any sibling schedules for
    # the same physical bus. Legacy (unlinked) schedules use their own id.
    bus_instance_id = new_id()
    if body.route_id:
        sibling = await db.schedules.find_one({
            "route_id": body.route_id,
            "departure_date": body.departure_date,
            "departure_time": body.departure_time,
        }, {"_id": 0, "bus_instance_id": 1})
        if sibling and sibling.get("bus_instance_id"):
            bus_instance_id = sibling["bus_instance_id"]
        # Block admins from creating exactly the SAME segment twice on the same
        # physical bus (would just duplicate the row for no reason). Different
        # from/to combos are allowed and expected.
        exact = await db.schedules.find_one({
            "route_id": body.route_id,
            "departure_date": body.departure_date,
            "departure_time": body.departure_time,
            "from_terminal_id": body.from_terminal_id,
            "to_terminal_id": body.to_terminal_id,
        })
        if exact:
            raise HTTPException(400,
                "A schedule for this exact segment already exists on this physical bus.")
    doc = body.model_dump()
    doc["currency"] = derived_currency
    doc["id"] = new_id()
    # Legacy schedules use their own id as bus_instance_id; route-linked ones
    # inherit from siblings (or mint their own if first of the group).
    doc["bus_instance_id"] = bus_instance_id if body.route_id else doc["id"]
    doc["layout_config"] = _layout_for_bus_type(doc.get("bus_type", "Standard"))
    doc["created_at"] = utcnow().isoformat()
    await db.schedules.insert_one(doc)
    doc.pop("_id", None)
    await log_audit(user, "create", "schedule", doc["id"], {
        "from_terminal_id": doc.get("from_terminal_id"),
        "to_terminal_id": doc.get("to_terminal_id"),
        "departure_date": doc.get("departure_date"),
        "departure_time": doc.get("departure_time"),
        "adult_fare": doc.get("adult_fare"),
        "total_seats": doc.get("total_seats"),
        "layout_config": doc.get("layout_config"),
        "currency": derived_currency,
        "route_id": doc.get("route_id"),
    }, request)
    return doc


@router.post("/admin/schedules/bulk")
async def admin_bulk_create_schedules(body: BulkScheduleBody, request: Request, user: dict = Depends(require_admin)):
    """Generate one schedule per date matching days_of_week between start_date and end_date (inclusive).
    Enforces: end_date >= start_date, max 180-day span, no open-ended schedules."""
    try:
        start = datetime.fromisoformat(body.start_date).date()
        end = datetime.fromisoformat(body.end_date).date()
    except ValueError:
        raise HTTPException(400, "Invalid start_date or end_date (expected YYYY-MM-DD)")
    if end < start:
        raise HTTPException(400, "end_date must be on or after start_date")
    span = (end - start).days + 1
    if span > 180:
        raise HTTPException(400, f"Date range too large ({span} days). Max 180 days per bulk create.")
    if not body.days_of_week or any(d < 0 or d > 6 for d in body.days_of_week):
        raise HTTPException(400, "days_of_week must be a non-empty list of 0..6 (0=Mon..6=Sun)")

    origin = await db.terminals.find_one({"id": body.from_terminal_id}, {"_id": 0})
    dest = await db.terminals.find_one({"id": body.to_terminal_id}, {"_id": 0})
    if not origin or not dest:
        raise HTTPException(400, "Invalid from_terminal_id or to_terminal_id")
    if body.from_terminal_id == body.to_terminal_id:
        raise HTTPException(400, "From and To terminals must differ")
    derived_currency = COUNTRY_TO_CURRENCY.get(origin.get("country", "MY"), "myr")

    # Validate optional route link once, up front. from/to must be among the
    # route's stops (but NOT force-set — admin picks which segment this schedule
    # sells). Sibling schedules for the same physical bus SHARE bus_instance_id.
    if body.route_id:
        route = await db.routes.find_one({"id": body.route_id}, {"_id": 0})
        if not route:
            raise HTTPException(400, "Linked route_id does not exist")
        boarding_ids = {s["terminal_id"] for s in route.get("boarding_stops", [])}
        alighting_ids = {s["terminal_id"] for s in route.get("alighting_stops", [])}
        if body.from_terminal_id not in boarding_ids:
            raise HTTPException(400, "from_terminal_id must be one of the route's boarding stops")
        if body.to_terminal_id not in alighting_ids:
            raise HTTPException(400, "to_terminal_id must be one of the route's alighting stops")

    dow_set = set(body.days_of_week)
    created = 0
    skipped = 0
    cur = start
    while cur <= end:
        if cur.weekday() in dow_set:
            # Dedup by EXACT segment (route_id + from + to + date + time). Multiple
            # segments per physical bus are allowed and expected.
            dupe_filter = {
                "from_terminal_id": body.from_terminal_id,
                "to_terminal_id": body.to_terminal_id,
                "departure_date": cur.isoformat(),
                "departure_time": body.departure_time,
            }
            if body.route_id:
                dupe_filter["route_id"] = body.route_id
            existing = await db.schedules.find_one(dupe_filter)
            if existing:
                skipped += 1
            else:
                # Look up a sibling schedule (same physical bus) to inherit its
                # bus_instance_id; otherwise mint a new one. Legacy (unlinked)
                # schedules use their own id — same as the migration path.
                sched_id = new_id()
                bus_instance_id = sched_id
                if body.route_id:
                    sibling = await db.schedules.find_one({
                        "route_id": body.route_id,
                        "departure_date": cur.isoformat(),
                        "departure_time": body.departure_time,
                    }, {"_id": 0, "bus_instance_id": 1})
                    if sibling and sibling.get("bus_instance_id"):
                        bus_instance_id = sibling["bus_instance_id"]
                    else:
                        bus_instance_id = new_id()
                doc = {
                    "id": sched_id,
                    "bus_instance_id": bus_instance_id,
                    "from_terminal_id": body.from_terminal_id,
                    "to_terminal_id": body.to_terminal_id,
                    "departure_date": cur.isoformat(),
                    "departure_time": body.departure_time,
                    "arrival_time": body.arrival_time,
                    "bus_operator": "Qistna Express",
                    "bus_type": body.bus_type,
                    "adult_fare": body.adult_fare,
                    "child_fare": body.child_fare,
                    "total_seats": body.total_seats,
                    "layout_config": _layout_for_bus_type(body.bus_type),
                    "currency": derived_currency,
                    "created_at": utcnow().isoformat(),
                }
                if body.route_id:
                    doc["route_id"] = body.route_id
                if body.trip_no:
                    doc["trip_no"] = body.trip_no
                await db.schedules.insert_one(doc)
                created += 1
        cur += timedelta(days=1)
    await log_audit(user, "bulk_create", "schedule", None, {
        "from_terminal_id": body.from_terminal_id,
        "to_terminal_id": body.to_terminal_id,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "days_of_week": body.days_of_week,
        "departure_time": body.departure_time,
        "created": created,
        "skipped_duplicates": skipped,
    }, request)
    return {
        "created": created,
        "skipped_duplicates": skipped,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "currency": derived_currency,
    }


@router.delete("/admin/schedules/range")
async def admin_delete_schedules_range(
    from_terminal_id: str,
    to_terminal_id: str,
    start_date: str,
    end_date: str,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Delete all schedules for a route within a date range. Blocks if any bookings exist for those schedules."""
    try:
        datetime.fromisoformat(start_date)
        datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")
    target = await db.schedules.find({
        "from_terminal_id": from_terminal_id,
        "to_terminal_id": to_terminal_id,
        "departure_date": {"$gte": start_date, "$lte": end_date},
    }, {"_id": 0, "id": 1}).to_list(1000)
    ids = [s["id"] for s in target]
    if not ids:
        return {"deleted": 0, "bookings_blocking": 0}
    # Block if any booking references these schedules
    booking_count = await db.bookings.count_documents({"schedule_id": {"$in": ids}})
    if booking_count > 0:
        raise HTTPException(400, f"Cannot delete — {booking_count} booking(s) exist for these schedules")
    result = await db.schedules.delete_many({"id": {"$in": ids}})
    await log_audit(user, "delete_range", "schedule", None, {
        "from_terminal_id": from_terminal_id,
        "to_terminal_id": to_terminal_id,
        "start_date": start_date,
        "end_date": end_date,
        "deleted": result.deleted_count,
    }, request)
    return {"deleted": result.deleted_count, "range": f"{start_date}..{end_date}"}


def _build_bulk_delete_query(f: BulkDeleteScheduleFilter) -> dict:
    """Convert a BulkDeleteScheduleFilter into a Mongo query.

    Raises HTTPException(400) if no filter is set at all — this is a safety
    guard so a bad UI form never accidentally deletes all 4,500+ schedules.
    """
    q: dict = {}
    if f.from_terminal_id:
        q["from_terminal_id"] = f.from_terminal_id
    if f.to_terminal_id:
        q["to_terminal_id"] = f.to_terminal_id
    if f.route_id:
        q["route_id"] = f.route_id
    if f.start_date or f.end_date:
        date_q: dict = {}
        if f.start_date:
            try:
                datetime.fromisoformat(f.start_date)
            except ValueError:
                raise HTTPException(400, "Invalid start_date (expected YYYY-MM-DD)")
            date_q["$gte"] = f.start_date
        if f.end_date:
            try:
                datetime.fromisoformat(f.end_date)
            except ValueError:
                raise HTTPException(400, "Invalid end_date (expected YYYY-MM-DD)")
            date_q["$lte"] = f.end_date
        q["departure_date"] = date_q
    if f.unlinked_only:
        # Match schedules that have no route_id at all OR an explicit null.
        q["$or"] = [{"route_id": {"$exists": False}}, {"route_id": None}]
    if not q:
        raise HTTPException(400, "At least one filter must be provided (safety guard).")
    return q


async def _notify_schedule_change(new_sched: dict, old_sched: dict) -> int:
    """Send a schedule-change email to every confirmed booking on this schedule.

    Fire-and-forget per booking so a single email failure doesn't block others.
    Returns the number of confirmed bookings we DISPATCHED emails for (Resend
    itself may still bounce later — we log each result inside email_service).
    """
    from email_service import send_schedule_change  # local import — avoids cycles
    bookings = await db.bookings.find(
        {"schedule_id": new_sched["id"], "status": "confirmed"},
        {"_id": 0},
    ).to_list(500)
    if not bookings:
        return 0
    from_term, to_term = await asyncio.gather(
        db.terminals.find_one({"id": new_sched.get("from_terminal_id")}, {"_id": 0}),
        db.terminals.find_one({"id": new_sched.get("to_terminal_id")}, {"_id": 0}),
    )
    old_departure = f"{old_sched.get('departure_date','')} · {old_sched.get('departure_time','')}"
    new_departure = f"{new_sched.get('departure_date','')} · {new_sched.get('departure_time','')}"
    for b in bookings:
        # Each dispatch is fire-and-forget so one failure doesn't cascade.
        asyncio.create_task(send_schedule_change(
            booking=b, from_term=from_term, to_term=to_term,
            old_departure=old_departure, new_departure=new_departure,
        ))
    return len(bookings)


@router.get("/admin/schedules/{schedule_id}/impact")
async def admin_schedule_impact(schedule_id: str, user: dict = Depends(require_admin)):
    """Return a summary of downstream impact BEFORE the admin edits a schedule.

    Used by the frontend edit modal to warn about changes that will affect
    real customers — number of confirmed bookings, distinct contact emails,
    and whether any of those bookings already have TBS QR passes issued
    (which may need reissuing if the departure time changes).
    """
    sched = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")
    bookings = await db.bookings.find(
        {"schedule_id": schedule_id, "status": {"$in": ["confirmed", "pending_payment"]}},
        {"_id": 0, "reference": 1, "contact_email": 1, "status": 1, "gohub_status": 1,
         "passengers": 1, "gohub_tickets": 1},
    ).to_list(500)
    confirmed = [b for b in bookings if b.get("status") == "confirmed"]
    with_cts_qr = [b for b in confirmed if (b.get("gohub_status") == "confirmed"
                                             and (b.get("gohub_tickets") or []))]
    total_seats = sum(len(b.get("passengers") or []) for b in confirmed)
    emails = sorted({b.get("contact_email") for b in confirmed if b.get("contact_email")})
    return {
        "schedule_id": schedule_id,
        "confirmed_bookings": len(confirmed),
        "pending_bookings": sum(1 for b in bookings if b.get("status") == "pending_payment"),
        "confirmed_passengers": total_seats,
        "bookings_with_cts_qr": len(with_cts_qr),
        "distinct_customer_emails": len(emails),
        "sample_references": [b.get("reference") for b in confirmed[:5]],
    }


@router.patch("/admin/schedules/{schedule_id}")
async def admin_update_schedule(schedule_id: str, body: ScheduleUpdateBody, request: Request,
                                user: dict = Depends(require_admin)):
    existing = await db.schedules.find_one({"id": schedule_id})
    if not existing:
        raise HTTPException(404, "Schedule not found")
    payload = body.model_dump(exclude_unset=True)
    notify = bool(payload.pop("notify_passengers", False))
    # Sentinel handling for `end_date`: empty string = clear the field
    # (via $unset), an actual date = set it, and omission leaves it alone.
    clear_end_date = payload.get("end_date") == ""
    if clear_end_date:
        payload.pop("end_date")
    updates = {k: v for k, v in payload.items() if v is not None}
    if "route_id" in updates:
        route = await db.routes.find_one({"id": updates["route_id"]}, {"_id": 0})
        if not route:
            raise HTTPException(400, "route_id does not exist")
        # Also ensure the schedule's from/to are within the route's boarding/alighting.
        boarding_ids = {s["terminal_id"] for s in route.get("boarding_stops", [])}
        alighting_ids = {s["terminal_id"] for s in route.get("alighting_stops", [])}
        if existing["from_terminal_id"] not in boarding_ids:
            raise HTTPException(400, "This schedule's from_terminal is not part of the target route's boarding stops")
        if existing["to_terminal_id"] not in alighting_ids:
            raise HTTPException(400, "This schedule's to_terminal is not part of the target route's alighting stops")
    if "bus_type" in updates:
        updates["layout_config"] = _layout_for_bus_type(updates["bus_type"])
    if not updates and not clear_end_date:
        raise HTTPException(400, "No changes provided")

    # Detect a customer-facing time/date change BEFORE the update so we can
    # notify only the affected trips (fare/seat changes don't warrant an email).
    time_or_date_changed = any(
        k in updates and updates[k] != existing.get(k)
        for k in ("departure_date", "departure_time")
    )

    mongo_op: dict = {}
    if updates:
        mongo_op["$set"] = updates
    if clear_end_date:
        mongo_op.setdefault("$unset", {})["end_date"] = ""
    result = await db.schedules.update_one({"id": schedule_id}, mongo_op)
    if result.matched_count == 0:
        raise HTTPException(404, "Schedule not found")
    doc = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    await log_audit(
        user, "update", "schedule", schedule_id,
        {"fields": list(updates.keys()) + (["end_date:cleared"] if clear_end_date else []),
         "notify_passengers": notify,
         "time_or_date_changed": time_or_date_changed},
        request,
    )

    notified_count = 0
    if notify and time_or_date_changed:
        # Fire-and-forget: never let email failures roll back a legitimate update.
        notified_count = await _notify_schedule_change(doc, existing)

    return {**doc, "notified_passengers": notified_count}


@router.post("/admin/schedules/bulk-delete/preview")
async def admin_bulk_delete_preview(body: BulkDeleteScheduleFilter, user: dict = Depends(require_admin)):
    """Return a count-only preview so admin can confirm before executing.

    - matched:    total schedules matching the filter
    - blocked:    those with ≥1 booking (cannot be safely deleted)
    - deletable:  matched - blocked
    """
    q = _build_bulk_delete_query(body)
    matched = await db.schedules.count_documents(q)
    if matched == 0:
        return {"matched": 0, "blocked": 0, "deletable": 0}
    # Look up which of those have bookings.
    ids = [s["id"] async for s in db.schedules.find(q, {"_id": 0, "id": 1})]
    blocking_ids = await db.bookings.distinct("schedule_id", {"schedule_id": {"$in": ids}})
    return {
        "matched": matched,
        "blocked": len(blocking_ids),
        "deletable": matched - len(blocking_ids),
    }


@router.post("/admin/schedules/bulk-delete")
async def admin_bulk_delete_execute(body: BulkDeleteScheduleFilter, request: Request,
                                    user: dict = Depends(require_admin)):
    """Delete matching schedules. By default skips any with bookings; set force=true to override."""
    q = _build_bulk_delete_query(body)
    docs = [s async for s in db.schedules.find(q, {"_id": 0, "id": 1})]
    ids = [s["id"] for s in docs]
    if not ids:
        return {"deleted": 0, "skipped": 0}
    blocking_ids = await db.bookings.distinct("schedule_id", {"schedule_id": {"$in": ids}})
    if body.force:
        target_ids = ids
        skipped = 0
    else:
        target_ids = [i for i in ids if i not in blocking_ids]
        skipped = len(blocking_ids)
    if not target_ids:
        return {"deleted": 0, "skipped": skipped, "note": "All matched schedules have bookings. Use force=true to override."}
    result = await db.schedules.delete_many({"id": {"$in": target_ids}})
    # Free any seat_locks tied to the deleted schedules.
    await db.seat_locks.delete_many({"schedule_id": {"$in": target_ids}})
    await log_audit(user, "bulk_delete", "schedule", None, {
        "filter": body.model_dump(exclude_unset=True),
        "deleted": result.deleted_count,
        "skipped_with_bookings": skipped,
        "force": body.force,
    }, request)
    return {"deleted": result.deleted_count, "skipped": skipped, "force": body.force}
