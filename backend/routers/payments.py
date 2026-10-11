"""Stripe payment, webhook, reconciliation, and admin reporting endpoints."""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import stripe as stripe_sdk
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionResponse,
    CheckoutStatusResponse,
    CheckoutSessionRequest,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.audit import log_audit
from core.config import db, logger, STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET, JWT_SECRET, JWT_ALG
from core.security import current_user, require_admin
from core.utils import new_id, utcnow
from services.gohub import _issue_gohub_tickets as issue_gohub_tickets
from services.settings import _get_settings

router = APIRouter()

class CheckoutBody(BaseModel):
    booking_id: str
    origin_url: str
    gateway: str = "card"  # stripe payment_method_types: card | grabpay | fpx
@lru_cache(maxsize=8)
def _payment_options_for(currency: str) -> list:
    """Return list of payment method options based on currency.
    All methods are processed through Stripe using the same merchant account.
    Option `id` maps directly to Stripe `payment_method_types`.
    - Card: universal (Visa/Mastercard/Amex) — all currencies
    - GrabPay: MY + SG markets (MYR, SGD)
    - FPX: MY online banking (MYR only)
    Cached because (a) input space is tiny (myr/sgd/usd) and (b) result is read-only.
    """
    c = (currency or "myr").lower()
    opts = [
        {
            "id": "card",
            "name": "Credit / Debit Card",
            "provider": "Stripe",
            "methods": ["Visa", "Mastercard", "Amex"],
            "available": True,
            "note": None,
        },
        {
            "id": "grabpay",
            "name": "GrabPay",
            "provider": "Stripe",
            "methods": ["GrabPay wallet"],
            "available": c in ("myr", "sgd"),
            "note": None if c in ("myr", "sgd") else "Not available for this currency",
        },
    ]
    if c == "myr":
        opts.append({
            "id": "fpx",
            "name": "FPX Online Banking",
            "provider": "Stripe",
            "methods": ["Maybank", "CIMB", "Public Bank", "RHB", "+ all Malaysian banks"],
            "available": True,
            "note": None,
        })
    return opts
@router.get("/payments/options/{booking_id}")
async def payment_options(
    booking_id: str,
    email: Optional[str] = None,
    user: Optional[dict] = Depends(current_user),
):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")

    is_owner = bool(user and booking.get("user_id") and user.get("id") == booking["user_id"])
    is_admin = bool(user and user.get("is_admin"))
    is_guest_match = bool(
        email
        and booking.get("contact_email")
        and email.strip().lower() == booking["contact_email"].strip().lower()
    )
    if not (is_owner or is_admin or is_guest_match):
        raise HTTPException(403, "You don't have access to this booking")

    currency = booking.get("pricing", {}).get("currency", "myr")
    return {
        "booking_id": booking_id,
        "currency": currency,
        "amount": booking.get("pricing", {}).get("total", 0),
        "options": _payment_options_for(currency),
    }
@router.post("/payments/checkout")
async def create_checkout(body: CheckoutBody, request: Request, user: Optional[dict] = Depends(current_user)):
    booking = await db.bookings.find_one({"id": body.booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["payment_status"] == "paid":
        raise HTTPException(400, "Booking already paid")

    # Single Stripe account — Malaysia. Always bill in MYR regardless of where
    # the trip departs from. Display currency on receipts can stay as-is (SGD
    # for SG departures), but the actual charge is converted to MYR using the
    # live sgd_to_myr_rate maintained in app settings.
    BILLING_CURRENCY = "myr"
    display_currency = (booking["pricing"].get("currency") or "myr").lower()
    display_total = float(booking["pricing"]["total"])
    if display_currency == BILLING_CURRENCY:
        amount = display_total
        fx_rate = 1.0
    else:
        # Currently we only support SGD → MYR. Anything else is misconfigured.
        if display_currency != "sgd":
            raise HTTPException(400, f"Cannot bill {display_currency.upper()} bookings — only MYR/SGD supported.")
        settings = await _get_settings()
        fx_rate = float(settings.get("sgd_to_myr_rate", DEFAULT_SGD_TO_MYR))
        amount = round(display_total * fx_rate, 2)

    # Gateway availability is now driven by the BILLING currency, not display.
    available_ids = {o["id"] for o in _payment_options_for(BILLING_CURRENCY) if o["available"]}
    if body.gateway not in available_ids:
        raise HTTPException(400, f"Payment method '{body.gateway}' is not available")

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)

    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&booking_id={body.booking_id}"
    cancel_url = f"{origin}/payment/cancel?booking_id={body.booking_id}"

    checkout_req = CheckoutSessionRequest(
        amount=amount,
        currency=BILLING_CURRENCY,
        success_url=success_url,
        cancel_url=cancel_url,
        payment_methods=[body.gateway],  # card | grabpay | fpx
        metadata={
            "booking_id": body.booking_id,
            "booking_reference": booking["reference"],
            "user_id": booking.get("user_id") or "guest",
            "payment_method": body.gateway,
            "display_currency": display_currency,
            "display_total": str(display_total),
            "fx_rate": str(fx_rate),
        },
    )
    try:
        session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)
    except Exception as e:
        # Stripe surfaces payment-method activation issues with a helpful message
        # (e.g. "The payment method type provided: grabpay is invalid"). Return
        # the exact message so the frontend can guide the operator to enable it
        # in the Stripe dashboard instead of showing a generic error.
        msg = str(e)
        logger.warning("Stripe create_checkout_session failed: %s", msg)
        if "invalid" in msg.lower() and body.gateway.lower() in msg.lower():
            raise HTTPException(
                422,
                detail={
                    "message": f"Payment method '{body.gateway}' isn't activated on this Stripe account yet. "
                               f"Enable it under Stripe Dashboard → Settings → Payment methods, "
                               f"or select Card to continue.",
                    "gateway": body.gateway,
                    "stripe_error": msg,
                },
            )
        raise HTTPException(502, detail={"message": f"Could not start Stripe checkout: {msg}"})

    await db.payment_transactions.insert_one(
        {
            "id": new_id(),
            "session_id": session.session_id,
            "booking_id": body.booking_id,
            "amount": amount,                  # MYR — what Stripe actually charges
            "currency": BILLING_CURRENCY,
            "display_amount": display_total,   # original price as shown to customer
            "display_currency": display_currency,
            "fx_rate": fx_rate,
            "payment_method": body.gateway,
            "user_id": booking.get("user_id"),
            "user_email": booking["contact_email"],
            "payment_status": "initiated",
            "status": "initiated",
            "metadata": {"booking_reference": booking["reference"], "payment_method": body.gateway},
            "created_at": utcnow().isoformat(),
            "updated_at": utcnow().isoformat(),
        }
    )

    return {"url": session.url, "session_id": session.session_id}
@router.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request):
    txn = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not txn:
        raise HTTPException(404, "Transaction not found")

    # If already finalized, return current state
    if txn.get("payment_status") == "paid" and txn.get("booking_finalized"):
        booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
        return {"payment_status": "paid", "status": txn.get("status"), "booking": booking}

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    try:
        check: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    except Exception as e:
        logger.warning("Stripe status fetch failed for %s: %s", session_id, e)
        booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
        return {
            "payment_status": txn.get("payment_status", "unknown"),
            "status": txn.get("status", "unknown"),
            "booking": booking,
            "error": "stripe_status_unavailable",
        }

    updates = {
        "status": check.status,
        "payment_status": check.payment_status,
        "updated_at": utcnow().isoformat(),
    }
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": updates})

    # Finalize booking idempotently
    if check.payment_status == "paid" and not txn.get("booking_finalized"):
        await _finalize_booking(txn["booking_id"], session_id)

    booking = await db.bookings.find_one({"id": txn["booking_id"]}, {"_id": 0})
    return {"payment_status": check.payment_status, "status": check.status, "booking": booking}
RECONCILE_MIN_AGE_SECONDS = 30
RECONCILE_MAX_AGE_SECONDS = 60 * 60 * 24  # ignore anything older than 24h
RECONCILE_INTERVAL_SECONDS = 60
async def _reconcile_one_transaction(txn: dict) -> dict:
    """Ask Stripe for the current status of a single session and finalize
    the booking if it's now paid. Returns a short summary dict for logging."""
    session_id = txn.get("session_id")
    if not session_id:
        return {"session_id": None, "action": "skip_missing_session_id"}
    if txn.get("booking_finalized"):
        return {"session_id": session_id, "action": "already_finalized"}

    # Reuse a fresh StripeCheckout — no request needed here.
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url="")
    try:
        check: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("reconcile: stripe status fetch failed for %s: %s", session_id, e)
        return {"session_id": session_id, "action": "stripe_error", "error": str(e)[:200]}

    await db.payment_transactions.update_one(
        {"session_id": session_id},
        {"$set": {
            "status": check.status,
            "payment_status": check.payment_status,
            "updated_at": utcnow().isoformat(),
        }},
    )
    if check.payment_status == "paid":
        await _finalize_booking(txn["booking_id"], session_id)
        logger.info("reconcile: FINALIZED booking for session=%s", session_id)
        return {
            "session_id": session_id, "booking_id": txn.get("booking_id"),
            "action": "finalized", "stripe_status": check.status,
        }
    return {
        "session_id": session_id, "booking_id": txn.get("booking_id"),
        "action": "still_pending", "stripe_status": check.status,
        "stripe_payment_status": check.payment_status,
    }
async def _reconcile_stuck_transactions() -> list[dict]:
    """Scan for `initiated` transactions in the 30s..24h age band and reconcile each."""
    now = utcnow()
    min_created = (now - timedelta(seconds=RECONCILE_MAX_AGE_SECONDS)).isoformat()
    max_created = (now - timedelta(seconds=RECONCILE_MIN_AGE_SECONDS)).isoformat()
    cursor = db.payment_transactions.find({
        "booking_finalized": {"$ne": True},
        "payment_status": {"$ne": "paid"},
        "created_at": {"$gte": min_created, "$lte": max_created},
    }, {"_id": 0}).limit(50)
    results: list[dict] = []
    async for txn in cursor:
        try:
            results.append(await _reconcile_one_transaction(txn))
        except Exception as e:  # noqa: BLE001
            logger.exception("reconcile: unexpected error for %s: %s", txn.get("session_id"), e)
    return results
async def _reconcile_loop() -> None:
    """Background task: reconcile stuck payments every RECONCILE_INTERVAL_SECONDS."""
    while True:
        try:
            outcomes = await _reconcile_stuck_transactions()
            actions = [o.get("action") for o in outcomes]
            finalized = sum(1 for a in actions if a == "finalized")
            if outcomes:
                logger.info("reconcile: scanned=%d finalized=%d", len(outcomes), finalized)
        except Exception as e:  # noqa: BLE001
            logger.exception("reconcile: loop error: %s", e)
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
@router.post("/admin/payments/reconcile")
async def admin_reconcile_payments(user: dict = Depends(require_admin)):
    """Force an immediate reconciliation sweep of all initiated transactions.

    Handy after the Stripe webhook has been disabled or the client-side
    polling window closed before an async payment (GrabPay/FPX) settled.
    """
    outcomes = await _reconcile_stuck_transactions()
    return {
        "checked": len(outcomes),
        "finalized": sum(1 for o in outcomes if o.get("action") == "finalized"),
        "results": outcomes,
    }
@router.post("/admin/payments/reconcile/{session_id}")
async def admin_reconcile_one(session_id: str, user: dict = Depends(require_admin)):
    """Force reconciliation for a single Stripe session — useful when a
    specific customer complains their paid booking is stuck."""
    txn = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not txn:
        raise HTTPException(404, "Transaction not found")
    return await _reconcile_one_transaction(txn)
async def _finalize_booking(booking_id: str, session_id: str):
    """Idempotent: mark seats booked, confirm booking, increment promo use, mark txn finalized.

    The confirmation email is dispatched from inside ``_issue_gohub_tickets``
    once the CTS attempt completes — so the email always reflects the final
    boarding-pass state (real CTS QR vs. fallback).
    """
    await db.seat_locks.update_many(
        {"booking_id": booking_id, "status": "locked"},
        {"$set": {"status": "booked", "expires_at": None}},
    )
    await db.bookings.update_one(
        {"id": booking_id},
        {"$set": {"status": "confirmed", "payment_status": "paid", "paid_at": utcnow().isoformat()}},
    )
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    promo = (booking or {}).get("pricing", {}).get("promo")
    if promo and promo.get("code"):
        await db.promo_codes.update_one({"code": promo["code"]}, {"$inc": {"used_count": 1}})
    await db.payment_transactions.update_one(
        {"session_id": session_id},
        {"$set": {"booking_finalized": True, "payment_status": "paid", "status": "complete"}},
    )
    if booking:
        # Fire-and-forget CTS QR issuance — includes the confirmation email dispatch.
        asyncio.create_task(_issue_gohub_tickets(booking_id))
@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """
    Stripe webhook receiver.

    Contract with Stripe:
      • Signature-verification failure  -> 400 (Stripe will retry with backoff, then disable).
      • Anything else                   -> 200 (even on business errors, so Stripe never disables us).
        Business errors are logged and can be reconciled from the Stripe dashboard.
    """
    body_bytes = await request.body()
    sig = request.headers.get("Stripe-Signature") or request.headers.get("stripe-signature")

    # 1. Parse + verify signature.
    event_dict: Optional[dict] = None
    if STRIPE_WEBHOOK_SECRET:
        if not sig:
            logger.warning("stripe_webhook: missing Stripe-Signature header")
            return JSONResponse(status_code=400, content={"error": "missing_signature"})
        try:
            event = stripe_sdk.Webhook.construct_event(
                body_bytes, sig, STRIPE_WEBHOOK_SECRET
            )
            event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        except stripe_sdk.error.SignatureVerificationError as e:
            logger.warning("stripe_webhook: signature verification failed: %s", e)
            return JSONResponse(status_code=400, content={"error": "invalid_signature"})
        except ValueError as e:
            # Invalid payload — return 400 so Stripe knows the body is malformed.
            logger.warning("stripe_webhook: invalid payload: %s", e)
            return JSONResponse(status_code=400, content={"error": "invalid_payload"})
        except Exception as e:
            # Any other verification-layer error: log and return 400 (do not fail 500).
            logger.exception("stripe_webhook: verification error: %s", e)
            return JSONResponse(status_code=400, content={"error": "verification_error"})
    else:
        # No secret configured (dev/preview). Parse JSON but skip verification.
        logger.warning(
            "stripe_webhook: STRIPE_WEBHOOK_SECRET is not set — accepting event without verification"
        )
        try:
            import json as _json
            event_dict = _json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            logger.exception("stripe_webhook: could not parse payload: %s", e)
            # Still return 200 so Stripe stops retrying garbage.
            return JSONResponse(status_code=200, content={"ok": True, "warning": "unparsable"})

    # 2. From here on, ALWAYS return 200 — business errors must not disable the endpoint.
    try:
        event_type = event_dict.get("type") if isinstance(event_dict, dict) else None
        event_id = event_dict.get("id") if isinstance(event_dict, dict) else None
        obj = (event_dict or {}).get("data", {}).get("object", {}) or {}

        session_id: Optional[str] = None
        payment_status: Optional[str] = None

        if event_type == "checkout.session.completed":
            session_id = obj.get("id")
            payment_status = obj.get("payment_status")
        elif event_type == "checkout.session.async_payment_succeeded":
            session_id = obj.get("id")
            payment_status = "paid"
        elif event_type == "checkout.session.async_payment_failed":
            session_id = obj.get("id")
            payment_status = "failed"
        elif event_type == "checkout.session.expired":
            session_id = obj.get("id")
            payment_status = obj.get("payment_status") or "expired"
        elif event_type == "payment_intent.succeeded":
            session_id = (obj.get("metadata") or {}).get("checkout_session_id")
            payment_status = "paid"
        elif event_type == "payment_intent.payment_failed":
            session_id = (obj.get("metadata") or {}).get("checkout_session_id")
            payment_status = "failed"
        else:
            # Any other event type: acknowledge and move on.
            logger.info("stripe_webhook: ignoring event type=%s id=%s", event_type, event_id)
            return JSONResponse(status_code=200, content={"ok": True, "ignored": event_type})

        logger.info(
            "stripe_webhook: type=%s session=%s payment_status=%s",
            event_type, session_id, payment_status,
        )

        # Finalize booking (idempotent). Guard every DB call.
        if payment_status == "paid" and session_id:
            try:
                txn = await db.payment_transactions.find_one({"session_id": session_id})
                if not txn:
                    logger.warning(
                        "stripe_webhook: no payment_transaction row for session=%s (webhook fired before checkout row?)",
                        session_id,
                    )
                elif txn.get("booking_finalized"):
                    logger.info("stripe_webhook: session=%s already finalized — skipping", session_id)
                else:
                    await _finalize_booking(txn["booking_id"], session_id)
                    logger.info("stripe_webhook: finalized booking for session=%s", session_id)
            except Exception as be:
                # Never let finalisation errors escape as 5xx to Stripe.
                logger.exception(
                    "stripe_webhook: finalize_booking failed for session=%s: %s", session_id, be
                )

        elif payment_status in ("failed", "expired") and session_id:
            try:
                await db.payment_transactions.update_one(
                    {"session_id": session_id},
                    {"$set": {"payment_status": payment_status, "updated_at": utcnow().isoformat()}},
                )
            except Exception as be:
                logger.exception(
                    "stripe_webhook: could not update payment_transaction for session=%s: %s",
                    session_id, be,
                )

        return JSONResponse(status_code=200, content={"ok": True, "event": event_type})

    except Exception as outer:
        # Absolute last-resort net: never 5xx to Stripe.
        logger.exception("stripe_webhook: unexpected error: %s", outer)
        return JSONResponse(status_code=200, content={"ok": True, "warning": "handler_error"})
@router.get("/admin/payments")
async def admin_payments(
    status_filter: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """List Stripe payment transactions joined with booking reference + customer email."""
    query: dict = {}
    if status_filter and status_filter != "all":
        query["payment_status"] = status_filter

    items = await db.payment_transactions.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)

    # Aggregate summary
    summary = {
        "total": len(items),
        "paid": 0,
        "initiated": 0,
        "failed": 0,
        "refunded": 0,
        "gross_myr": 0.0,
        "gross_sgd": 0.0,
    }
    for t in items:
        ps = (t.get("payment_status") or "initiated").lower()
        if ps == "paid":
            summary["paid"] += 1
            ccy = (t.get("currency") or "myr").lower()
            key = "gross_myr" if ccy == "myr" else "gross_sgd" if ccy == "sgd" else None
            if key:
                summary[key] += float(t.get("amount") or 0)
        elif ps == "failed":
            summary["failed"] += 1
        elif ps in ("refunded", "canceled", "expired"):
            summary["refunded"] += 1
        else:
            summary["initiated"] += 1

    return {"summary": summary, "items": items}
@router.get("/admin/audit-logs")
async def admin_audit_logs(
    limit: int = 200,
    resource: Optional[str] = None,
    action: Optional[str] = None,
    actor_email: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """List admin audit log entries, newest first."""
    query: dict = {}
    if resource and resource != "all":
        query["resource"] = resource
    if action and action != "all":
        query["action"] = action
    if actor_email:
        safe_email = re.escape(actor_email.strip()[:120])
        query["actor_email"] = {"$regex": safe_email, "$options": "i"}
    limit = max(1, min(limit, 1000))
    items = await db.audit_logs.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"total": len(items), "items": items}
