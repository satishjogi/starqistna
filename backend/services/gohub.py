"""Shared GoHub/CTS ticket issuance business logic."""
import asyncio
import os

from core.config import db, logger
from core.utils import utcnow
from email_service import send_booking_confirmation

async def _issue_gohub_tickets(booking_id: str) -> None:
    """After payment confirms, call CTS `getOnlineQR_V2` to fetch the real QR
    ticket(s) for a booking. Fire-and-forget: never crash the caller.

    Result is persisted onto the booking as:
      * ``gohub_status``     — "confirmed" | "skipped" | "failed"
      * ``gohub_tickets``    — [{seat_number, opetickno, tickno, qr}, ...]
      * ``gohub_error``      — {code, message}  (only when status == failed)
      * ``gohub_processed_at`` — ISO timestamp

    When ``GOHUB_ENABLED`` is off, or the from/to terminals lack a ``cts_code``,
    or the schedule lacks a ``trip_no``, we mark it ``skipped`` — the operator
    can retry from the admin panel once TBS finishes their config.

    Finally, always dispatches the booking-confirmation email (regardless of
    CTS outcome) so the customer always receives a receipt.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        logger.warning("gohub: booking %s vanished before ticket issuance", booking_id)
        return

    now_iso = utcnow().isoformat()

    # Load everything we need for the CTS call in parallel.
    schedule, from_term, to_term = await asyncio.gather(
        db.schedules.find_one({"id": booking.get("schedule_id")}, {"_id": 0}),
        db.terminals.find_one({"id": booking.get("from_terminal_id")}, {"_id": 0}),
        db.terminals.find_one({"id": booking.get("to_terminal_id")}, {"_id": 0}),
    )

    # Pre-flight validation — anything missing means we skip, not fail.
    reasons: list[str] = []
    if os.environ.get("GOHUB_ENABLED", "false").strip().lower() not in ("1", "true", "yes"):
        reasons.append("GOHUB_ENABLED=false")
    if not schedule or not (schedule.get("trip_no") or "").strip():
        reasons.append("schedule.trip_no missing")
    if not from_term or not (from_term.get("cts_code") or "").strip():
        reasons.append("from_terminal.cts_code missing")
    if not to_term or not (to_term.get("cts_code") or "").strip():
        reasons.append("to_terminal.cts_code missing")

    async def _send_email_with_current_state() -> None:
        """Reload the booking (so the email sees the latest gohub_* fields)
        and fire the confirmation email — never lets errors escape."""
        try:
            fresh = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
            if fresh:
                await send_booking_confirmation(fresh, from_term, to_term)
        except Exception:  # noqa: BLE001
            logger.exception("gohub: email dispatch failed for booking %s", booking_id)

    if reasons:
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "skipped",
                "gohub_skip_reason": "; ".join(reasons),
                "gohub_processed_at": now_iso,
            }},
        )
        logger.info("gohub: skipped booking %s (%s)", booking.get("reference"), ", ".join(reasons))
        await _send_email_with_current_state()
        return

    from gohub_client import GoHubClient, GoHubError, make_opetickno

    booking_ref = booking.get("reference") or booking_id[:8].upper()
    # Adult and child fares live on the schedule; use them as sprice per seattype.
    adult_fare = float(schedule.get("adult_fare") or 0.0)
    child_fare = float(schedule.get("child_fare") or (adult_fare * 0.5))

    # Build one CTS <detail> per passenger. CTS caps opetickno at 20 chars —
    # make_opetickno enforces that and raises early if it would overflow.
    seats: list[dict] = []
    for pax in booking.get("passengers", []):
        seat_no = str(pax.get("seat_number") or "").strip()
        category = (pax.get("category") or "adult").lower()
        seattype = "C" if category == "child" else "A"
        seats.append({
            "opetickno": make_opetickno(booking_ref, seat_no),
            "seatno": seat_no,
            "seattype": seattype,
            "sprice": child_fare if seattype == "C" else adult_fare,
            "name": (pax.get("name") or "").strip()[:50] or "Passenger",
            "ic": (pax.get("ic_or_passport") or "").strip()[:14],
            "contact": (booking.get("contact_phone") or "").strip()[:14],
        })

    client = GoHubClient(db=db, timeout=20)
    try:
        result = await client.get_qr(
            trans_id=booking_ref,
            trip_no=schedule["trip_no"],
            trip_date=schedule["departure_date"],
            depart_date=schedule["departure_date"],
            depart_time=schedule["departure_time"],
            from_counter=from_term["cts_code"],
            to_counter=to_term["cts_code"],
            seats=seats,
        )
    except GoHubError as e:
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "failed",
                "gohub_error": {"code": e.code, "message": e.message},
                "gohub_processed_at": now_iso,
            }},
        )
        logger.warning("gohub: booking %s failed [%s] %s", booking_ref, e.code, e.message)
        await _send_email_with_current_state()
        return
    except Exception as e:  # noqa: BLE001 — never propagate to caller
        await db.bookings.update_one(
            {"id": booking_id},
            {"$set": {
                "gohub_status": "failed",
                "gohub_error": {"code": "UNEXPECTED", "message": str(e)[:250]},
                "gohub_processed_at": now_iso,
            }},
        )
        logger.exception("gohub: unexpected error for booking %s", booking_ref)
        await _send_email_with_current_state()
        return

    # Parse per-passenger detail rows out of the flattened response.
    # `_parse_response` flattens `<detail .../>` children as ``detail_<attr>``
    # keys — since CTS returns MULTIPLE `<detail>` we surface only the last
    # one via the flattened dict. The full XML lives in ``gohub_logs``.
    #
    # For every seat we also fetch the CTS-branded QR image (with gopass logo)
    # from their separate image endpoint — in parallel to keep this fast.
    # If any image fetch fails, we simply don't store one and the frontend
    # falls back to a locally-generated plain QR.
    raw_qr = result.get("detail_QR") or result.get("QR") or result.get("qr") or ""
    image_bytes_list = await asyncio.gather(
        *(client.fetch_qr_image(raw_qr) for _ in seats)
    ) if raw_qr else [None] * len(seats)

    tickets: list[dict] = []
    for seat, img_bytes in zip(seats, image_bytes_list):
        ticket_entry = {
            "seat_number": seat["seatno"],
            "opetickno": seat["opetickno"],
            "tickno": result.get("detail_tickno") or result.get("tickno") or "",
            "qr": raw_qr,
        }
        if img_bytes:
            import base64 as _b64
            ticket_entry["qr_image"] = "data:image/png;base64," + _b64.b64encode(img_bytes).decode("ascii")
        tickets.append(ticket_entry)

    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {
            "gohub_status": "confirmed",
            "gohub_tickets": tickets,
            "gohub_processed_at": now_iso,
        }, "$unset": {"gohub_error": ""}},
    )
    logger.info("gohub: booking %s issued %d ticket(s)", booking_ref, len(tickets))
    await _send_email_with_current_state()
