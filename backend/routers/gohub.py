"""GoHub/CTS ticket issuance and admin probe endpoints."""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.audit import log_audit
from core.config import db
from core.security import require_admin
from services.gohub import _issue_gohub_tickets as issue_gohub_tickets

router = APIRouter()

@router.post("/admin/bookings/{booking_id}/gohub/retry")
async def admin_gohub_retry(booking_id: str, user: dict = Depends(require_admin)):
    """Manually retry CTS ticket issuance for a booking whose earlier attempt
    was skipped or failed (typical use: TBS finished uploading a rate)."""
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking.get("payment_status") != "paid":
        raise HTTPException(409, "Booking is not paid — nothing to issue")
    await _issue_gohub_tickets(booking_id)
    fresh = await db.bookings.find_one(
        {"id": booking_id},
        {"_id": 0, "gohub_status": 1, "gohub_tickets": 1,
         "gohub_error": 1, "gohub_processed_at": 1, "reference": 1},
    )
    return fresh or {"gohub_status": "unknown"}
class GoHubProbeBody(BaseModel):
    trip_no: str = "SQ001"
    boarding_date: Optional[str] = None   # YYYYMMDD; defaults to today MY
    boarding_time: str = "2330"
    seat: str = "1A"
    seat_type: Literal["A", "C", "S", "O"] = "A"
    from_counter: str = "TBS01"
    to_counter: str = "GMC01"
    ic_no: str = ""
    contact: str = ""
    confirm: bool = False
    cancel: bool = False
@router.post("/admin/gohub/probe")
async def admin_gohub_probe(body: GoHubProbeBody, request: Request, user: dict = Depends(require_admin)):
    """Fire a single CTS reserve (optionally confirm/query/cancel) round-trip.

    Meant for post-deploy smoke checks and TBS diagnostics — captures every
    step so an operator can share the result back to TBS support if a call
    fails. Persisted to `gohub_logs` like any other CTS call.
    """
    from gohub_client import GoHubClient, GoHubError, make_opetickno  # local import — keep server startup light
    import uuid as _uuid

    my_today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    trans_id = f"PROBE-{_uuid.uuid4().hex[:12].upper()}"
    client = GoHubClient(db=db, timeout=20)
    steps: list[dict] = []

    # One probe = one seat + one passenger. Compose a spec-compliant opetickno
    # from the trans_id (which acts as our synthetic "booking ref" here).
    probe_ref = trans_id.split("-", 1)[1][:8]
    opetickno = make_opetickno(probe_ref, body.seat)
    probe_seat = {
        "opetickno": opetickno,
        "seatno": body.seat,
        "seattype": body.seat_type,
        "sprice": 55.0,
        "name": "Test Passenger",
        "ic": body.ic_no,
        "contact": body.contact,
    }

    # 1) reserve
    try:
        reserve = await client.reserve_qr(
            trans_id=trans_id, trip_no=body.trip_no,
            trip_date=body.boarding_date or my_today,
            depart_date=body.boarding_date or my_today,
            depart_time=body.boarding_time,
            from_counter=body.from_counter, to_counter=body.to_counter,
            seats=[probe_seat],
        )
        steps.append({"step": "reserve", "ok": True, "data": reserve})
    except GoHubError as e:
        steps.append({"step": "reserve", "ok": False, "error": {"code": e.code, "message": e.message}})
        await log_audit(user, "probe", "gohub", trans_id, {"steps": steps}, request)
        return {"trans_id": trans_id, "steps": steps}

    reserved_id = reserve.get("reservedid") or reserve.get("ReservedID")
    if body.confirm and reserved_id:
        try:
            confirm = await client.confirm_qr(
                reserved_id=reserved_id,
                seats=[{"opetickno": opetickno, "newopetickno": opetickno}],
            )
            steps.append({"step": "confirm", "ok": True, "data": confirm})
        except GoHubError as e:
            steps.append({"step": "confirm", "ok": False, "error": {"code": e.code, "message": e.message}})

    # Query is always by trans_id (not opetickno)
    try:
        q = await client.query_qr(trans_id=trans_id)
        steps.append({"step": "query", "ok": True, "data": q})
    except GoHubError as e:
        steps.append({"step": "query", "ok": False, "error": {"code": e.code, "message": e.message}})

    if body.cancel:
        try:
            c = await client.cancel_qr(trans_id=trans_id, opeticknos=[opetickno])
            steps.append({"step": "cancel", "ok": True, "data": c})
        except GoHubError as e:
            steps.append({"step": "cancel", "ok": False, "error": {"code": e.code, "message": e.message}})

    await log_audit(user, "probe", "gohub", trans_id, {"steps_count": len(steps)}, request)

    # Extract the resulting QR string (from confirm if run, else from reserve) and
    # encode it as a PNG data URL so the demo page can render it directly.
    qr_string = None
    for step in reversed(steps):
        if step["ok"] and step["data"].get("qr"):
            qr_string = step["data"]["qr"]
            break
    qr_png_data_url = None
    if qr_string:
        try:
            import qrcode, base64, io
            img = qrcode.make(qr_string)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_png_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            logger.warning("QR image render failed: %s", e)

    return {
        "trans_id": trans_id,
        "steps": steps,
        "qr_string": qr_string,
        "qr_png_data_url": qr_png_data_url,
    }
