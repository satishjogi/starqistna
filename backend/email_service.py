"""Email delivery — Resend API. Non-blocking, failure-tolerant."""
import os
import base64
import logging
import asyncio
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

import qrcode
import resend

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger("transit.email")

resend.api_key = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Star Qistna <onboarding@resend.dev>")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO", "support@starqistna.com")
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://starqistna.com")


def _resend_ready() -> bool:
    """True only when a real-looking Resend key is configured.

    Also rejects the ``re_replace_me`` placeholder from ``.env.example`` so
    operators don't ship a stale template and wonder why no emails arrive.
    """
    key = (resend.api_key or "").strip()
    if not key:
        return False
    if key.lower() in {"re_replace_me", "replace_me", "changeme", "your_key_here"}:
        return False
    return key.startswith("re_")


def _currency_symbol(ccy: str) -> str:
    return {"myr": "RM", "sgd": "S$", "usd": "$"}.get((ccy or "myr").lower(), ccy.upper())


def _qr_png_base64(payload: str) -> str:
    img = qrcode.make(payload, box_size=6, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _render_ticket_html(booking: dict, from_term: dict, to_term: dict) -> str:
    ref = booking.get("reference", "")
    pricing = booking.get("pricing", {})
    ccy = _currency_symbol(pricing.get("currency", "myr"))
    total = f"{ccy} {float(pricing.get('total', 0)):.2f}"

    # Map seat_number → CTS ticket (if TBS issued QR passes for this booking).
    gohub_tickets = booking.get("gohub_tickets") or []
    tick_by_seat = {t.get("seat_number"): t for t in gohub_tickets if t.get("qr")}
    has_cts_passes = bool(tick_by_seat)

    def _pax_row(p: dict) -> str:
        seat = p.get("seat_number", "")
        cts = tick_by_seat.get(seat)
        # Each CTS QR is attached as its own CID (seat "1A" → CID "qr-1A").
        qr_cell = ""
        if cts:
            qr_cell = (
                f'<td align="right" style="padding:10px 0 10px 12px;border-top:1px dashed #e4e4e7;vertical-align:middle;width:96px">'
                f'<img alt="QR seat {seat}" src="cid:qr-{seat}" width="88" height="88" '
                f'style="display:block;background:#fff;padding:4px;border:1px solid #e4e4e7" />'
                f'<div style="font-family:monospace;font-size:9px;color:#71717a;margin-top:4px">'
                f'{cts.get("tickno","")}</div>'
                f'</td>'
            )
        return f"""<tr>
              <td style="padding:10px 0;border-top:1px dashed #e4e4e7;font-family:monospace;font-weight:700;font-size:13px">{seat}</td>
              <td style="padding:10px 0 10px 12px;border-top:1px dashed #e4e4e7;font-size:14px">
                <div style="font-weight:700">{p.get('name','')}</div>
                <div style="color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:.1em">{p.get('category','adult')}</div>
              </td>
              {qr_cell}
            </tr>"""

    pax_rows = "".join(_pax_row(p) for p in booking.get("passengers", []))

    # Header QR:
    #  * CTS confirmed → point at the first CTS QR (the "one to scan at TBS")
    #  * otherwise → fall back to the booking reference so the customer always
    #    has something to show at the counter while TBS finalises the route.
    if has_cts_passes:
        header_qr_src = f"cid:qr-{list(tick_by_seat.keys())[0]}"
        header_caption = "SCAN AT TBS BOARDING GATE"
    else:
        header_qr_src = "cid:qrcode"
        header_caption = "BOARDING PASS IS BEING PREPARED · SHOW REFERENCE AT COUNTER"

    # Status banner: friendly message when CTS didn't issue a real QR yet.
    gohub_status = (booking.get("gohub_status") or "").lower()
    if gohub_status == "confirmed" and has_cts_passes:
        banner = (
            '<div style="margin:20px 0;padding:14px 16px;background:#ecfdf5;border:1px solid #10b981;color:#065f46;font-size:13px">'
            '<b>Boarding pass ready.</b> Scan the QR next to each passenger at the TBS gate.</div>'
        )
    elif gohub_status == "failed":
        banner = (
            '<div style="margin:20px 0;padding:14px 16px;background:#fef3c7;border:1px solid #f59e0b;color:#78350f;font-size:13px">'
            '<b>Boarding pass being finalised.</b> Our team is preparing your TBS QR pass — we\'ll email it separately before departure. '
            'You can still show this booking reference at the counter.</div>'
        )
    elif gohub_status == "skipped":
        banner = (
            '<div style="margin:20px 0;padding:14px 16px;background:#eff6ff;border:1px solid #3b82f6;color:#1e3a8a;font-size:13px">'
            '<b>Your seat is confirmed.</b> Show this booking reference at the boarding counter.</div>'
        )
    else:
        banner = ""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Your Star Qistna ticket</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#09090b">
  <div style="max-width:640px;margin:32px auto;background:#fff;border:1px solid #e4e4e7">
    <div style="background:#002FA7;color:#fff;padding:22px 28px;font-weight:900;letter-spacing:-.5px;font-size:22px">
      STAR QISTNA <span style="font-size:11px;letter-spacing:.2em;font-weight:600;opacity:.85;margin-left:10px">FIRST CLASS COACH</span>
    </div>
    <div style="padding:28px">
      <div style="text-transform:uppercase;letter-spacing:.22em;color:#16a34a;font-size:11px;font-weight:700">Confirmed</div>
      <h1 style="margin:8px 0 4px;font-size:36px;letter-spacing:-1px;font-weight:900">Seat secured.</h1>
      <p style="color:#52525b;font-size:14px;margin:0 0 24px">Show this ticket at the boarding gate. One QR per passenger.</p>

      {banner}

      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;border-left:4px solid #002FA7">
        <tr>
          <td style="padding:20px 24px;vertical-align:middle">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#52525b;font-size:10px;font-weight:700">Booking reference</div>
            <div style="font-family:monospace;font-weight:900;font-size:28px;letter-spacing:1px;margin-top:4px">{ref}</div>
            <div style="text-transform:uppercase;letter-spacing:.18em;color:#52525b;font-size:9px;font-weight:700;margin-top:8px">{header_caption}</div>
          </td>
          <td align="right" style="padding:16px 24px;vertical-align:middle">
            <img alt="QR {ref}" src="{header_qr_src}" width="128" height="128" style="display:block;background:#fff;padding:6px;border:1px solid #e4e4e7" />
          </td>
        </tr>
      </table>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:28px">
        <tr>
          <td style="padding:0 16px 16px 0;width:50%;vertical-align:top">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#71717a;font-size:10px;font-weight:700">From</div>
            <div style="font-size:22px;font-weight:900;line-height:1.1;margin-top:4px">{(from_term or {}).get('city','—')}</div>
            <div style="font-family:monospace;color:#71717a;font-size:11px">{(from_term or {}).get('code','')} · {(from_term or {}).get('name','')}</div>
          </td>
          <td style="padding:0 0 16px 16px;width:50%;vertical-align:top">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#71717a;font-size:10px;font-weight:700">To</div>
            <div style="font-size:22px;font-weight:900;line-height:1.1;margin-top:4px">{(to_term or {}).get('city','—')}</div>
            <div style="font-family:monospace;color:#71717a;font-size:11px">{(to_term or {}).get('code','')} · {(to_term or {}).get('name','')}</div>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 16px 0 0;vertical-align:top">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#71717a;font-size:10px;font-weight:700">Date · Departure</div>
            <div style="font-family:monospace;font-weight:700;margin-top:4px">{booking.get('departure_date','')} · {booking.get('departure_time','')}</div>
          </td>
          <td style="padding:16px 0 0 16px;vertical-align:top">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#71717a;font-size:10px;font-weight:700">Total paid</div>
            <div style="font-family:monospace;font-weight:900;font-size:18px;margin-top:4px">{total}</div>
          </td>
        </tr>
      </table>

      <div style="margin-top:28px">
        <div style="text-transform:uppercase;letter-spacing:.2em;color:#52525b;font-size:10px;font-weight:700;margin-bottom:6px">Passengers</div>
        <table width="100%" cellpadding="0" cellspacing="0">{pax_rows}</table>
      </div>

      <div style="margin-top:28px;padding-top:20px;border-top:1px solid #e4e4e7;font-size:12px;color:#52525b;line-height:1.6">
        <p><b>Please arrive 30 minutes before departure.</b> The coach will not wait for late passengers.</p>
        <p>Need to cancel or change? <a href="{PUBLIC_APP_URL}/bookings/{booking.get('id','')}" style="color:#002FA7;font-weight:700">View booking</a> · <a href="{PUBLIC_APP_URL}/refund" style="color:#002FA7">Refund policy</a></p>
      </div>
    </div>
    <div style="background:#09090b;color:#a1a1aa;padding:16px 28px;font-family:monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase">
      © STAR QISTNA · PREMIUM COACH · <a href="{PUBLIC_APP_URL}" style="color:#a1a1aa;text-decoration:none">STARQISTNA.COM</a>
    </div>
  </div>
</body></html>"""


def _send_sync(to_email: str, subject: str, html: str, attachments: list = None) -> dict:
    payload = {
        "from": EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html,
        "reply_to": [EMAIL_REPLY_TO],
    }
    if attachments:
        payload["attachments"] = attachments
    return resend.Emails.send(payload)


async def send_booking_confirmation(booking: dict, from_term: dict, to_term: dict):
    """Non-blocking best-effort: log on failure, do not raise into the booking path.

    When CTS has issued real QR passes (``booking.gohub_tickets``), one PNG
    attachment is created per seat with Content-ID ``qr-{seat_number}``. The
    HTML body picks these up inline via ``<img src="cid:qr-1A">`` etc.

    When no CTS pass exists yet (skipped/failed/pending), we fall back to a
    single booking-reference QR attached as ``cid:qrcode`` so the customer
    can still show something at the counter.
    """
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping email")
        return
    to_email = booking.get("contact_email")
    if not to_email:
        logger.warning("Booking %s has no contact_email; skipping email", booking.get("id"))
        return
    html = _render_ticket_html(booking, from_term, to_term)
    subject = f"Star Qistna — Booking confirmed · {booking.get('reference','')}"

    attachments: list[dict] = []
    gohub_tickets = [t for t in (booking.get("gohub_tickets") or []) if t.get("qr")]
    if gohub_tickets:
        # One CID per CTS QR — email clients render each inline next to its passenger row.
        for t in gohub_tickets:
            seat = t.get("seat_number", "?")
            attachments.append({
                "filename": f"boarding-{seat}.png",
                "content": _qr_png_base64(t["qr"]),
                "content_type": "image/png",
                "content_id": f"qr-{seat}",
            })
    else:
        # Fallback so the header <img cid:qrcode> still has something to render.
        ref = booking.get("reference", "ticket")
        attachments.append({
            "filename": f"{ref}.png",
            "content": _qr_png_base64(ref),
            "content_type": "image/png",
            "content_id": "qrcode",
        })

    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html, attachments)
        logger.info(
            "Sent booking email to %s ref=%s cts_passes=%d id=%s",
            to_email, booking.get("reference"), len(gohub_tickets), res.get("id"),
        )
    except Exception as e:
        logger.exception("Failed to send booking email to %s: %s", to_email, e)


def _render_password_reset_html(full_name: str, reset_link: str, ttl_minutes: int) -> str:
    safe_name = (full_name or "there").split()[0]
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reset your Star Qistna password</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#09090b">
  <div style="max-width:560px;margin:32px auto;background:#fff;border:1px solid #e4e4e7">
    <div style="background:#002FA7;color:#fff;padding:22px 28px;font-weight:900;letter-spacing:-.5px;font-size:22px">
      STAR QISTNA <span style="font-size:11px;letter-spacing:.2em;font-weight:600;opacity:.85;margin-left:10px">FIRST CLASS COACH</span>
    </div>
    <div style="padding:32px 28px">
      <div style="text-transform:uppercase;letter-spacing:.22em;color:#002FA7;font-size:11px;font-weight:700">Account security</div>
      <h1 style="margin:8px 0 12px;font-size:30px;letter-spacing:-.5px;font-weight:900">Reset your password</h1>
      <p style="font-size:14px;color:#52525b;line-height:1.6;margin:0 0 18px">
        Hi {safe_name}, we received a request to reset the password for your Star Qistna account.
        Click the button below to choose a new one — the link expires in <b>{ttl_minutes} minutes</b>.
      </p>
      <p style="margin:24px 0">
        <a href="{reset_link}" style="display:inline-block;background:#002FA7;color:#fff;padding:14px 28px;text-decoration:none;font-weight:700;letter-spacing:.05em">Reset password →</a>
      </p>
      <p style="font-size:12px;color:#71717a;line-height:1.6;margin:0 0 8px">
        Or paste this link into your browser:
      </p>
      <p style="font-family:monospace;font-size:12px;background:#f4f4f5;padding:10px 12px;word-break:break-all;border:1px solid #e4e4e7">
        {reset_link}
      </p>
      <p style="font-size:12px;color:#71717a;line-height:1.6;margin-top:22px">
        Didn't request this? You can safely ignore this email — your password won't change until you create a new one.
      </p>
    </div>
    <div style="background:#09090b;color:#a1a1aa;padding:16px 28px;font-family:monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase">
      © STAR QISTNA · PREMIUM COACH · <a href="{PUBLIC_APP_URL}" style="color:#a1a1aa;text-decoration:none">STARQISTNA.COM</a>
    </div>
  </div>
</body></html>"""


async def send_password_reset(to_email: str, full_name: str, reset_link: str, ttl_minutes: int):
    """Non-blocking; logs and swallows delivery errors."""
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping password-reset email to %s", to_email)
        return
    html = _render_password_reset_html(full_name, reset_link, ttl_minutes)
    subject = "Star Qistna — Reset your password"
    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html, None)
        logger.info("Sent password-reset email to %s id=%s", to_email, res.get("id"))
    except Exception as e:
        logger.exception("Failed to send password-reset email to %s: %s", to_email, e)


def _render_admin_invite_html(full_name: str, inviter_name: str, role: str, accept_link: str, ttl_days: int) -> str:
    safe_name = (full_name or "there").split()[0]
    role_label = "Super-admin" if role == "super_admin" else "Admin"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>You've been invited to Star Qistna admin</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#09090b">
  <div style="max-width:560px;margin:32px auto;background:#fff;border:1px solid #e4e4e7">
    <div style="background:#002FA7;color:#fff;padding:22px 28px;font-weight:900;letter-spacing:-.5px;font-size:22px">
      STAR QISTNA <span style="font-size:11px;letter-spacing:.2em;font-weight:600;opacity:.85;margin-left:10px">ADMIN ACCESS</span>
    </div>
    <div style="padding:32px 28px">
      <div style="text-transform:uppercase;letter-spacing:.22em;color:#002FA7;font-size:11px;font-weight:700">You're invited</div>
      <h1 style="margin:8px 0 12px;font-size:30px;letter-spacing:-.5px;font-weight:900">Join as {role_label}</h1>
      <p style="font-size:14px;color:#52525b;line-height:1.6;margin:0 0 18px">
        Hi {safe_name}, <b>{inviter_name}</b> has invited you to join the Star Qistna admin team as <b>{role_label}</b>.
        Click below to set your password and accept the invite. This link is valid for {ttl_days} days.
      </p>
      <p style="margin:24px 0">
        <a href="{accept_link}" style="display:inline-block;background:#002FA7;color:#fff;padding:14px 28px;text-decoration:none;font-weight:700;letter-spacing:.05em">Accept invite →</a>
      </p>
      <p style="font-size:12px;color:#71717a;line-height:1.6;margin:0 0 8px">
        Or paste this link into your browser:
      </p>
      <p style="font-family:monospace;font-size:12px;background:#f4f4f5;padding:10px 12px;word-break:break-all;border:1px solid #e4e4e7">
        {accept_link}
      </p>
      <p style="font-size:12px;color:#71717a;line-height:1.6;margin-top:22px">
        Didn't expect this invite? You can safely ignore this email — no account will be created until the link is used.
      </p>
    </div>
    <div style="background:#09090b;color:#a1a1aa;padding:16px 28px;font-family:monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase">
      © STAR QISTNA · ADMIN CONSOLE · <a href="{PUBLIC_APP_URL}" style="color:#a1a1aa;text-decoration:none">STARQISTNA.COM</a>
    </div>
  </div>
</body></html>"""


async def send_admin_invite(to_email: str, full_name: str, inviter_name: str, role: str, accept_link: str, ttl_days: int):
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping admin-invite email to %s", to_email)
        return
    html = _render_admin_invite_html(full_name, inviter_name, role, accept_link, ttl_days)
    subject = f"Star Qistna — You're invited as {'Super-admin' if role == 'super_admin' else 'Admin'}"
    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html, None)
        logger.info("Sent admin-invite email to %s id=%s", to_email, res.get("id"))
    except Exception as e:
        logger.exception("Failed to send admin-invite email to %s: %s", to_email, e)


_CATEGORY_LABELS = {
    "general": "General",
    "booking_issue": "Booking issue",
    "complaint": "Complaint",
    "suggestion": "Suggestion",
    "praise": "Praise",
}


def _render_feedback_user_html(name: str, reference: str, category: str, message: str) -> str:
    safe_name = (name or "there").split()[0]
    cat_label = _CATEGORY_LABELS.get(category, category.title())
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>We received your feedback</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#09090b">
  <div style="max-width:560px;margin:32px auto;background:#fff;border:1px solid #e4e4e7">
    <div style="background:#002FA7;color:#fff;padding:22px 28px;font-weight:900;letter-spacing:-.5px;font-size:22px">
      STAR QISTNA <span style="font-size:11px;letter-spacing:.2em;font-weight:600;opacity:.85;margin-left:10px">CUSTOMER SUPPORT</span>
    </div>
    <div style="padding:32px 28px">
      <div style="text-transform:uppercase;letter-spacing:.22em;color:#002FA7;font-size:11px;font-weight:700">Feedback received</div>
      <h1 style="margin:8px 0 12px;font-size:28px;letter-spacing:-.5px;font-weight:900">Thanks, {safe_name}.</h1>
      <p style="font-size:14px;color:#52525b;line-height:1.6;margin:0 0 18px">
        We've got your message and a human from our support team will read it. Expect a reply within 24 hours on working days.
      </p>
      <div style="background:#f4f4f5;border:1px solid #e4e4e7;padding:14px 16px;margin:18px 0;font-family:monospace;font-size:12px">
        <div><b>Reference:</b> {reference}</div>
        <div><b>Category:</b> {cat_label}</div>
      </div>
      <div style="font-size:11px;font-family:monospace;color:#71717a;letter-spacing:.1em;text-transform:uppercase;margin-top:16px">Your message</div>
      <blockquote style="font-size:14px;color:#27272a;border-left:3px solid #002FA7;padding:8px 14px;margin:8px 0 0;white-space:pre-wrap">{message}</blockquote>
      <p style="font-size:12px;color:#71717a;line-height:1.6;margin-top:22px">
        Please quote reference <b>{reference}</b> if you reach out again on the same matter.
      </p>
    </div>
    <div style="background:#09090b;color:#a1a1aa;padding:16px 28px;font-family:monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase">
      © STAR QISTNA · <a href="{PUBLIC_APP_URL}" style="color:#a1a1aa;text-decoration:none">STARQISTNA.COM</a>
    </div>
  </div>
</body></html>"""


def _render_feedback_admin_html(fb: dict) -> str:
    cat_label = _CATEGORY_LABELS.get(fb.get("category", ""), fb.get("category", "").title())
    rating = fb.get("rating")
    rating_row = f"<div><b>Rating:</b> {'★' * rating}{'☆' * (5 - rating)}</div>" if rating else ""
    ref_row = f"<div><b>Booking ref:</b> {fb['booking_reference']}</div>" if fb.get("booking_reference") else ""
    name_row = f"<div><b>Name:</b> {fb['name']}</div>" if fb.get("name") else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>New customer feedback</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#09090b">
  <div style="max-width:620px;margin:32px auto;background:#fff;border:1px solid #e4e4e7">
    <div style="background:#B5121B;color:#fff;padding:18px 28px;font-weight:900;letter-spacing:-.5px;font-size:18px">
      NEW FEEDBACK · {fb['reference']}
      <span style="float:right;font-size:10px;letter-spacing:.2em;font-weight:700;opacity:.85">{cat_label.upper()}</span>
    </div>
    <div style="padding:24px 28px">
      <div style="font-family:monospace;font-size:12px;line-height:1.8;color:#27272a">
        {name_row}
        <div><b>Email:</b> <a href="mailto:{fb['email']}" style="color:#002FA7">{fb['email']}</a></div>
        {ref_row}
        {rating_row}
        <div><b>Submitted:</b> {fb['created_at']}</div>
        <div><b>IP:</b> {fb.get('ip') or '—'}</div>
      </div>
      <div style="font-size:11px;font-family:monospace;color:#71717a;letter-spacing:.1em;text-transform:uppercase;margin-top:20px">Message</div>
      <blockquote style="font-size:14px;color:#09090b;border-left:3px solid #B5121B;padding:10px 16px;margin:6px 0 0;background:#fafafa;white-space:pre-wrap">{fb['message']}</blockquote>
      <p style="margin-top:22px">
        <a href="{PUBLIC_APP_URL}/admin" style="display:inline-block;background:#002FA7;color:#fff;padding:10px 18px;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:.05em">Open admin panel →</a>
      </p>
    </div>
  </div>
</body></html>"""


async def send_feedback_confirmation(to_email: str, name: str, reference: str, category: str, message: str):
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping feedback-confirm email to %s", to_email)
        return
    html = _render_feedback_user_html(name, reference, category, message)
    subject = f"Star Qistna — We received your feedback ({reference})"
    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html, None)
        logger.info("Sent feedback confirmation to %s id=%s", to_email, res.get("id"))
    except Exception as e:
        logger.exception("Failed to send feedback confirmation to %s: %s", to_email, e)


async def send_feedback_admin_notification(admin_email: str, feedback: dict):
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping feedback admin notification")
        return
    html = _render_feedback_admin_html(feedback)
    cat = _CATEGORY_LABELS.get(feedback.get("category", ""), feedback.get("category", ""))
    subject = f"[Feedback · {cat}] {feedback['reference']} from {feedback['email']}"
    try:
        res = await asyncio.to_thread(_send_sync, admin_email, subject, html, None)
        logger.info("Sent feedback admin notification to %s id=%s", admin_email, res.get("id"))
    except Exception as e:
        logger.exception("Failed to send feedback admin notification: %s", e)



def _render_cancelled_html(booking: dict, from_term: dict, to_term: dict, refunded: bool, amount: float, currency: str) -> str:
    ref = booking.get("reference", "")
    ccy_sym = _currency_symbol(currency)
    route = f"{(from_term or {}).get('city', '')} → {(to_term or {}).get('city', '')}"
    dep = f"{booking.get('departure_date', '')} · {booking.get('departure_time', '')}"
    if refunded:
        headline = "Refund issued"
        sub = f"Your booking <b>{ref}</b> has been cancelled and a full refund of <b>{ccy_sym} {amount:.2f}</b> has been issued."
        body_extra = "Refunds typically appear on your card or wallet within 5–10 business days, depending on your bank."
        accent = "#0E8C5C"
    else:
        headline = "Booking cancelled"
        sub = f"Your booking <b>{ref}</b> has been cancelled."
        body_extra = "Because the cancellation was made within 24 hours of departure, this ticket is non-refundable as per our policy."
        accent = "#B5121B"
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#fafafa;font-family:Arial,Helvetica,sans-serif;color:#111;">
  <div style="max-width:560px;margin:0 auto;background:#fff;padding:32px 28px;border:1px solid #eee;">
    <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#999;margin-bottom:8px;">Star Qistna · Cancellation</div>
    <h1 style="font-size:26px;margin:0 0 14px 0;color:{accent};">{headline}</h1>
    <p style="font-size:15px;line-height:1.5;margin:0 0 14px 0;">{sub}</p>
    <table style="width:100%;border-collapse:collapse;margin:18px 0;">
      <tr><td style="padding:6px 0;font-size:12px;color:#666;width:120px;">Reference</td><td style="padding:6px 0;font-size:13px;font-family:monospace;font-weight:bold;">{ref}</td></tr>
      <tr><td style="padding:6px 0;font-size:12px;color:#666;">Route</td><td style="padding:6px 0;font-size:13px;">{route}</td></tr>
      <tr><td style="padding:6px 0;font-size:12px;color:#666;">Departure</td><td style="padding:6px 0;font-size:13px;font-family:monospace;">{dep}</td></tr>
    </table>
    <p style="font-size:13px;line-height:1.6;color:#555;margin:0 0 18px 0;">{body_extra}</p>
    <div style="border-top:1px solid #eee;padding-top:16px;font-size:11px;color:#999;line-height:1.6;">
      Need help? Reply to this email and our team will get back to you.<br/>
      Star Qistna · First Class Massage Coach
    </div>
  </div>
</body></html>"""


async def send_booking_cancelled(booking: dict, from_term: dict, to_term: dict, refunded: bool, amount: float, currency: str):
    if not _resend_ready():
        logger.warning("RESEND_API_KEY not set; skipping cancellation email")
        return
    to_email = booking.get("contact_email")
    if not to_email:
        return
    html = _render_cancelled_html(booking, from_term, to_term, refunded, amount, currency)
    subject = f"Star Qistna — Booking cancelled · {booking.get('reference', '')}"
    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html, None)
        logger.info("Sent cancellation email to %s ref=%s id=%s", to_email, booking.get("reference"), res.get("id"))
    except Exception as e:
        logger.exception("Failed to send cancellation email to %s: %s", to_email, e)
