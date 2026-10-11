"""Booking, cancellation, and boarding endpoints."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

import stripe as stripe_sdk
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from core.audit import log_audit
from core.config import db, logger
from core.pricing import _calc_pricing
from core.security import current_user, require_admin, require_user
from core.utils import new_id, utcnow
from services.promos import _find_valid_promo

router = APIRouter()

class PassengerInput(BaseModel):
    name: str
    category: Literal["adult", "child"]
    ic_or_passport: Optional[str] = None
class CreateBookingBody(BaseModel):
    schedule_id: str
    seat_assignments: List[dict]  # [{seat_number, passenger_index}]
    passengers: List[PassengerInput]
    contact_email: EmailStr
    contact_phone: str
    promo_code: Optional[str] = None
    # When booking a specific segment of a multi-stop route, these override the
    # schedule's own from/to and switch fare pricing to the route's pairing entry.
    # If omitted, the schedule's origin→final-destination is assumed (legacy).
    pickup_terminal_id: Optional[str] = None
    dropoff_terminal_id: Optional[str] = None
class BoardingValidateBody(BaseModel):
    reference: str
    gate: Optional[str] = None
    mark_boarded: bool = True
async def _resolve_segment_pricing(sched: dict,
                                   pickup_terminal_id: Optional[str],
                                   dropoff_terminal_id: Optional[str]) -> dict:
    """Return a sched-shaped dict whose fare fields reflect the correct segment.

    - If the schedule has no linked route, returns `sched` untouched.
    - If the caller didn't specify pickup/dropoff, defaults to the schedule's own
      from/to (which for route-linked schedules is the route's origin→final).
    - If the requested segment isn't a valid route pairing → HTTP 400.
    """
    if not sched.get("route_id"):
        return sched
    # Route-linked. Default to the schedule's own from/to if caller didn't specify.
    pickup_id = pickup_terminal_id or sched["from_terminal_id"]
    dropoff_id = dropoff_terminal_id or sched["to_terminal_id"]
    # If the requested segment matches the schedule's own from/to and the schedule
    # already stores a fare, keep the schedule's fare (it was set at creation).
    if pickup_id == sched.get("from_terminal_id") and dropoff_id == sched.get("to_terminal_id"):
        return sched
    route = await db.routes.find_one({"id": sched["route_id"]}, {"_id": 0})
    if not route:
        raise HTTPException(400, "Linked route not found — cannot resolve segment fare.")
    pairing = next(
        (p for p in route.get("pairings", [])
         if p.get("pickup_id") == pickup_id and p.get("dropoff_id") == dropoff_id),
        None,
    )
    if not pairing:
        raise HTTPException(400, "Requested pickup/drop-off pair is not offered on this route.")
    # Return a shallow copy with the pairing's fare + currency baked in so that
    # `_calc_pricing` and the booking record reflect the sold segment.
    seg = dict(sched)
    seg["from_terminal_id"] = pickup_id
    seg["to_terminal_id"] = dropoff_id
    seg["adult_fare"] = float(pairing.get("adult_fare", sched.get("adult_fare")))
    seg["child_fare"] = float(pairing.get("child_fare", sched.get("child_fare", 0)))
    seg["currency"] = (pairing.get("currency") or sched.get("currency") or "myr").lower()
    return seg
@router.post("/boarding/validate")
async def boarding_validate(body: BoardingValidateBody):
    """Endpoint for existing gate-scanners. QR encodes only the booking `reference` (plain text).
    The scanner posts the reference here; we return booking details and atomically mark as boarded."""
    ref = body.reference.strip().upper()
    booking = await db.bookings.find_one({"reference": ref}, {"_id": 0})
    if not booking:
        return {"valid": False, "reason": "not_found", "reference": ref}
    if booking.get("status") != "confirmed":
        return {"valid": False, "reason": "not_confirmed", "status": booking.get("status"), "reference": ref}
    sched = await db.schedules.find_one({"id": booking["schedule_id"]}, {"_id": 0})
    # Already boarded?
    already = bool(booking.get("boarded_at"))
    if body.mark_boarded and not already:
        await db.bookings.update_one(
            {"reference": ref},
            {"$set": {"boarded_at": utcnow().isoformat(), "boarded_gate": body.gate}},
        )
        booking["boarded_at"] = utcnow().isoformat()
        booking["boarded_gate"] = body.gate
    return {
        "valid": True,
        "already_boarded": already,
        "reference": ref,
        "schedule": sched,
        "passengers": booking.get("passengers", []),
        "seats": booking.get("seats", []),
        "from_terminal_id": booking.get("from_terminal_id"),
        "to_terminal_id": booking.get("to_terminal_id"),
        "departure_date": booking.get("departure_date"),
        "departure_time": booking.get("departure_time"),
        "boarded_at": booking.get("boarded_at"),
    }
@router.post("/bookings")
async def create_booking(body: CreateBookingBody, user: Optional[dict] = Depends(current_user)):
    sched = await db.schedules.find_one({"id": body.schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")

    # Block new bookings on a schedule the operator has retired via end_date.
    # Existing bookings on the same schedule remain untouched.
    end_date = (sched.get("end_date") or "").strip()
    if end_date and end_date < sched.get("departure_date", ""):
        raise HTTPException(
            410,
            detail={
                "message": "This departure is no longer available for new bookings.",
                "code": "schedule_retired",
            },
        )

    if len(body.passengers) != len(body.seat_assignments):
        raise HTTPException(400, "Passengers and seat assignments must match in count")

    seats = [sa["seat_number"] for sa in body.seat_assignments]
    # verify locks exist (any owner) and aren't booked yet -- must belong to this session ideally
    current_locks = await db.seat_locks.find(
        {"schedule_id": body.schedule_id, "seat_number": {"$in": seats}},
        {"_id": 0},
    ).to_list(50)
    lock_map = {lk["seat_number"]: lk for lk in current_locks}
    missing = [s for s in seats if s not in lock_map or lock_map[s]["status"] != "locked"]
    if missing:
        raise HTTPException(409, detail={"message": "Seat locks missing or expired", "seats": missing})

    # Resolve the specific segment (pickup/drop-off + fare) — for route-linked
    # schedules this returns a sched-shaped dict with pairing fares; otherwise
    # returns `sched` unchanged.
    segment = await _resolve_segment_pricing(
        sched, body.pickup_terminal_id, body.dropoff_terminal_id,
    )

    pricing_promo = None
    if body.promo_code:
        pricing_promo = await _find_valid_promo(body.promo_code, segment.get("currency", "myr"))
    pricing = _calc_pricing(segment, [p.model_dump() for p in body.passengers], pricing_promo)

    booking_id = new_id()
    # Build passenger <-> seat mapping
    pax_with_seat = []
    for sa in body.seat_assignments:
        idx = sa["passenger_index"]
        p = body.passengers[idx].model_dump()
        p["seat_number"] = sa["seat_number"]
        pax_with_seat.append(p)

    booking_ref = "SQ" + booking_id.replace("-", "")[:8].upper()
    doc = {
        "id": booking_id,
        "reference": booking_ref,
        "schedule_id": body.schedule_id,
        # The from/to on the booking always reflect the SOLD segment (pickup/drop-off
        # the passenger actually chose), NOT the physical bus endpoints.
        "from_terminal_id": segment["from_terminal_id"],
        "to_terminal_id": segment["to_terminal_id"],
        "departure_date": sched["departure_date"],
        "departure_time": sched["departure_time"],
        "user_id": user["id"] if user else None,
        "contact_email": body.contact_email.lower(),
        "contact_phone": body.contact_phone,
        "passengers": pax_with_seat,
        "seats": seats,
        "pricing": pricing,
        "status": "pending_payment",
        "payment_status": "pending",
        "created_at": utcnow().isoformat(),
    }
    await db.bookings.insert_one(doc)

    # Attach booking_id to locks
    await db.seat_locks.update_many(
        {"schedule_id": body.schedule_id, "seat_number": {"$in": seats}},
        {"$set": {"booking_id": booking_id}},
    )

    return {k: v for k, v in doc.items() if k != "_id"}
@router.get("/bookings/me")
async def my_bookings(user: dict = Depends(require_user)):
    items = await db.bookings.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    if not items:
        return items
    # Batch-fetch all referenced terminals in a single query (replaces N+1 lookups).
    term_ids = {b["from_terminal_id"] for b in items} | {b["to_terminal_id"] for b in items}
    terms = {
        t["id"]: t
        async for t in db.terminals.find({"id": {"$in": list(term_ids)}}, {"_id": 0})
    }
    for b in items:
        b["from"] = terms.get(b["from_terminal_id"])
        b["to"] = terms.get(b["to_terminal_id"])
    return items
@router.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: str,
    email: Optional[str] = None,
    user: Optional[dict] = Depends(current_user),
):
    """Allow:
      - the booking owner (authenticated user_id match)
      - any admin
      - a guest who knows id + the contact_email used at checkout (?email=)
    """
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")

    is_owner = bool(user and b.get("user_id") and user.get("id") == b["user_id"])
    is_admin = bool(user and user.get("is_admin"))
    is_guest_match = bool(
        email
        and b.get("contact_email")
        and email.strip().lower() == b["contact_email"].strip().lower()
    )
    if not (is_owner or is_admin or is_guest_match):
        raise HTTPException(403, "You don't have access to this booking")

    b["from"] = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
    b["to"] = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
    b["schedule"] = await db.schedules.find_one({"id": b["schedule_id"]}, {"_id": 0})
    return b
CANCEL_REFUND_THRESHOLD_HOURS = 24
def _hours_to_departure(booking: dict) -> float:
    """Hours between now and the booking's departure (MY/SG local time, UTC+8)."""
    try:
        dep_local = datetime.fromisoformat(f"{booking['departure_date']}T{booking['departure_time']}:00")
        dep_utc = dep_local.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        return (dep_utc - utcnow()).total_seconds() / 3600.0
    except Exception:
        return 0.0
@router.get("/bookings/{booking_id}/cancellation-quote")
async def cancellation_quote(booking_id: str, user: dict = Depends(require_user)):
    """Tell the user, before they confirm, exactly what cancelling now will do."""
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "Not your booking")
    if b.get("status") != "confirmed":
        raise HTTPException(400, f"Cannot cancel a booking in status '{b.get('status')}'")
    hours = _hours_to_departure(b)
    eligible = hours >= CANCEL_REFUND_THRESHOLD_HOURS
    pricing = b.get("pricing", {})

    # Stripe charged in MYR — quote the refund in the actual billed currency
    # so the customer sees on the modal what will appear on their statement.
    refund_amount = 0.0
    refund_currency = (pricing.get("currency") or "myr").upper()
    if eligible and float(pricing.get("total", 0)) > 0:
        txn = await db.payment_transactions.find_one(
            {"booking_id": booking_id, "payment_status": "paid"},
            sort=[("created_at", -1)],
        )
        if txn:
            refund_amount = float(txn.get("amount", 0))
            refund_currency = (txn.get("currency") or "myr").upper()
        else:
            refund_amount = float(pricing.get("total", 0))
    return {
        "booking_id": b["id"],
        "reference": b.get("reference"),
        "hours_to_departure": round(hours, 2),
        "threshold_hours": CANCEL_REFUND_THRESHOLD_HOURS,
        "refund_eligible": eligible,
        "refund_amount": refund_amount,
        "currency": refund_currency,
        "outcome": "full_refund" if eligible else "ticket_burned",
        "policy": "Free cancellation up to 24 hours before departure. Within 24 hours, the ticket is non-refundable.",
    }
def _stripe_refund_sync(payment_intent_id: str, amount_minor_units: int, idem_key: str) -> dict:
    """Run the blocking Stripe refund call inside a worker thread."""
    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_API_KEY
    return stripe_sdk.Refund.create(
        payment_intent=payment_intent_id,
        amount=amount_minor_units,
        reason="requested_by_customer",
        idempotency_key=idem_key,
    )
def _stripe_get_payment_intent_sync(session_id: str) -> Optional[str]:
    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_API_KEY
    sess = stripe_sdk.checkout.Session.retrieve(session_id)
    pi = sess.get("payment_intent") if isinstance(sess, dict) else getattr(sess, "payment_intent", None)
    return pi if isinstance(pi, str) else None
@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, user: dict = Depends(require_user)):
    b = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "Not your booking")
    if b.get("status") != "confirmed":
        raise HTTPException(400, f"Cannot cancel a booking in status '{b.get('status')}'")

    hours = _hours_to_departure(b)
    eligible = hours >= CANCEL_REFUND_THRESHOLD_HOURS
    pricing = b.get("pricing", {})
    currency = (pricing.get("currency") or "myr").lower()
    total = float(pricing.get("total", 0))
    refund_info = {"refunded": False, "amount": 0.0, "currency": currency.upper(), "stripe_refund_id": None}

    if eligible and total > 0:
        # Pull the most recent paid txn for this booking
        txn = await db.payment_transactions.find_one(
            {"booking_id": booking_id, "payment_status": "paid"},
            sort=[("created_at", -1)],
        )
        if not txn or not txn.get("session_id"):
            raise HTTPException(400, "No paid Stripe session found for this booking; cannot refund automatically.")
        # Refund must use the actual amount + currency Stripe charged (MYR),
        # NOT the booking's display total — which may be in SGD.
        charged_amount = float(txn.get("amount", total))
        charged_currency = (txn.get("currency") or "myr").upper()
        try:
            pi = await asyncio.to_thread(_stripe_get_payment_intent_sync, txn["session_id"])
            if not pi:
                raise HTTPException(400, "Stripe session has no payment_intent yet.")
            # Stripe expects amounts in minor units (cents/sen)
            amount_minor = int(round(charged_amount * 100))
            idem_key = f"refund:{booking_id}"
            refund = await asyncio.to_thread(_stripe_refund_sync, pi, amount_minor, idem_key)
            refund_info = {
                "refunded": True,
                "amount": charged_amount,
                "currency": charged_currency,
                "stripe_refund_id": refund.get("id") if isinstance(refund, dict) else getattr(refund, "id", None),
            }
            await db.payment_transactions.update_one(
                {"session_id": txn["session_id"]},
                {"$set": {"payment_status": "refunded", "refunded_at": utcnow().isoformat(),
                          "refund_amount": charged_amount, "refund_id": refund_info["stripe_refund_id"]}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Stripe refund failed for booking %s: %s", booking_id, e)
            raise HTTPException(502, f"Refund failed: {e}")
    elif eligible and total == 0:
        # Free booking (e.g. 100% promo): no money to refund, but cancellation is still
        # "eligible" — treat as a clean cancellation, not a burn.
        refund_info = {"refunded": True, "amount": 0.0, "currency": currency.upper(), "stripe_refund_id": None}

    # Free up the seats so they can be re-sold
    await db.seat_locks.delete_many({"booking_id": booking_id})

    new_status = "cancelled_refunded" if refund_info["refunded"] else "cancelled_burned"
    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {
            "status": new_status,
            "cancelled_at": utcnow().isoformat(),
            "cancelled_by_user_id": user["id"],
            "cancellation_outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
            "cancellation_refund": refund_info,
        }},
    )

    # Best-effort email
    try:
        from_term = await db.terminals.find_one({"id": b["from_terminal_id"]}, {"_id": 0})
        to_term = await db.terminals.find_one({"id": b["to_terminal_id"]}, {"_id": 0})
        from email_service import send_booking_cancelled
        asyncio.create_task(send_booking_cancelled(b, from_term, to_term, refund_info["refunded"], refund_info["amount"], refund_info["currency"]))
    except Exception as e:
        logger.warning("Cancellation email queue failed: %s", e)

    log_audit_task = log_audit(user, "cancel", "booking", booking_id, {
        "outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
        "hours_to_departure": round(hours, 2),
        "amount": refund_info["amount"],
        "currency": refund_info["currency"],
    })
    asyncio.create_task(log_audit_task)

    return {
        "ok": True,
        "booking_id": booking_id,
        "status": new_status,
        "outcome": "full_refund" if refund_info["refunded"] else "ticket_burned",
        "refund": refund_info,
        "hours_to_departure": round(hours, 2),
    }
@router.get("/admin/stats")
async def admin_stats(user: dict = Depends(require_admin)):
    # Run all 5 counts concurrently — ~5× faster than sequential awaits.
    users_n, bookings_n, confirmed_n, terminals_n, schedules_n = await asyncio.gather(
        db.users.count_documents({}),
        db.bookings.count_documents({}),
        db.bookings.count_documents({"status": "confirmed"}),
        db.terminals.count_documents({}),
        db.schedules.count_documents({}),
    )
    return {
        "users": users_n,
        "bookings": bookings_n,
        "confirmed_bookings": confirmed_n,
        "terminals": terminals_n,
        "schedules": schedules_n,
    }
@router.get("/admin/bookings")
async def admin_bookings(user: dict = Depends(require_admin)):
    items = await db.bookings.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items
