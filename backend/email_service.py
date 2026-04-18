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

    pax_rows = "".join(
        f"""<tr>
              <td style="padding:10px 0;border-top:1px dashed #e4e4e7;font-family:monospace;font-weight:700;font-size:13px">{p.get('seat_number','')}</td>
              <td style="padding:10px 0 10px 12px;border-top:1px dashed #e4e4e7;font-size:14px">
                <div style="font-weight:700">{p.get('name','')}</div>
                <div style="color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:.1em">{p.get('category','adult')}</div>
              </td>
            </tr>"""
        for p in booking.get("passengers", [])
    )

    qr = _qr_png_base64(ref)

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
      <p style="color:#52525b;font-size:14px;margin:0 0 24px">Show this ticket at the boarding gate. One QR per booking.</p>

      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;border-left:4px solid #002FA7">
        <tr>
          <td style="padding:20px 24px;vertical-align:middle">
            <div style="text-transform:uppercase;letter-spacing:.2em;color:#52525b;font-size:10px;font-weight:700">Booking reference</div>
            <div style="font-family:monospace;font-weight:900;font-size:28px;letter-spacing:1px;margin-top:4px">{ref}</div>
          </td>
          <td align="right" style="padding:16px 24px;vertical-align:middle">
            <img alt="QR" src="data:image/png;base64,{qr}" width="128" height="128" style="display:block;background:#fff;padding:6px;border:1px solid #e4e4e7" />
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


def _send_sync(to_email: str, subject: str, html: str) -> dict:
    return resend.Emails.send({
        "from": EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html,
        "reply_to": [EMAIL_REPLY_TO],
    })


async def send_booking_confirmation(booking: dict, from_term: dict, to_term: dict):
    """Non-blocking best-effort: log on failure, do not raise into the booking path."""
    if not resend.api_key:
        logger.warning("RESEND_API_KEY not set; skipping email")
        return
    to_email = booking.get("contact_email")
    if not to_email:
        logger.warning("Booking %s has no contact_email; skipping email", booking.get("id"))
        return
    html = _render_ticket_html(booking, from_term, to_term)
    subject = f"Star Qistna — Booking confirmed · {booking.get('reference','')}"
    try:
        res = await asyncio.to_thread(_send_sync, to_email, subject, html)
        logger.info("Sent booking email to %s ref=%s id=%s", to_email, booking.get("reference"), res.get("id"))
    except Exception as e:
        logger.exception("Failed to send booking email to %s: %s", to_email, e)
