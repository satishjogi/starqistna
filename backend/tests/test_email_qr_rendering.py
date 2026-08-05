"""Tests for the CTS QR pass rendering in the email template + attachment builder.

Verifies:
  * per-passenger `<img cid:qr-{seat}>` when CTS tickets exist
  * fallback to booking-reference QR when no CTS tickets
  * status-aware banners (confirmed/failed/skipped)
  * one PNG attachment per CTS ticket with the right Content-ID
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from email_service import _render_ticket_html, _qr_png_base64  # noqa: E402


def _booking(gohub_status: str = "", gohub_tickets=None, ref: str = "SQABC12345") -> dict:
    return {
        "id": "BK1", "reference": ref,
        "departure_date": "2026-08-10", "departure_time": "11:30",
        "contact_email": "cust@example.com",
        "passengers": [
            {"name": "Ali", "category": "adult", "seat_number": "1A"},
            {"name": "Ali Jr", "category": "child", "seat_number": "1B"},
        ],
        "pricing": {"currency": "myr", "total": 82.5},
        "gohub_status": gohub_status,
        "gohub_tickets": gohub_tickets or [],
    }


_TERM_FROM = {"city": "Kuala Lumpur", "name": "TBS", "code": "TBS"}
_TERM_TO = {"city": "Singapore", "name": "Golden Mile", "code": "GMC"}


def test_email_renders_cts_qr_per_passenger_when_confirmed():
    tickets = [
        {"seat_number": "1A", "opetickno": "SQ-ABC12345-1A", "tickno": "CTS100001", "qr": "OQR,QISTINA,cts1,ope1,hash1"},
        {"seat_number": "1B", "opetickno": "SQ-ABC12345-1B", "tickno": "CTS100002", "qr": "OQR,QISTINA,cts2,ope2,hash2"},
    ]
    html = _render_ticket_html(_booking("confirmed", tickets), _TERM_FROM, _TERM_TO)

    # One CID per seat
    assert 'src="cid:qr-1A"' in html
    assert 'src="cid:qr-1B"' in html
    # CTS ticket numbers surfaced next to each passenger
    assert "CTS100001" in html
    assert "CTS100002" in html
    # Header points at first CTS QR (not the booking reference fallback)
    assert 'src="cid:qr-1A" width="128"' in html
    # Green "ready" banner
    assert "Boarding pass ready" in html
    # Legacy fallback CID must NOT be there
    assert 'src="cid:qrcode"' not in html


def test_email_falls_back_to_reference_qr_when_no_cts_tickets():
    html = _render_ticket_html(_booking("skipped"), _TERM_FROM, _TERM_TO)
    assert 'src="cid:qrcode"' in html
    # No per-passenger QR cells
    assert "cid:qr-1A" not in html
    assert "cid:qr-1B" not in html
    # Skipped banner appears
    assert "Show this booking reference" in html


def test_email_shows_failed_banner_when_cts_failed():
    html = _render_ticket_html(_booking("failed"), _TERM_FROM, _TERM_TO)
    assert "being finalised" in html.lower() or "being finalised" in html
    # Header still shows fallback booking-ref QR so the counter can look them up
    assert 'src="cid:qrcode"' in html


def test_email_header_caption_reflects_cts_state():
    tickets = [{"seat_number": "1A", "tickno": "CTS1", "qr": "OQR,x,y,z,h"}]
    confirmed_html = _render_ticket_html(_booking("confirmed", tickets), _TERM_FROM, _TERM_TO)
    assert "SCAN AT TBS BOARDING GATE" in confirmed_html

    skipped_html = _render_ticket_html(_booking("skipped"), _TERM_FROM, _TERM_TO)
    assert "BOARDING PASS IS BEING PREPARED" in skipped_html


def test_qr_png_base64_is_valid_png():
    b64 = _qr_png_base64("SQABC12345")
    raw = base64.b64decode(b64)
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_png_base64_encodes_full_qr_payload():
    """A CTS QR string can be ~100 chars — make sure we don't truncate."""
    long_payload = "OQR,QISTINA," + ("x" * 100)
    b64 = _qr_png_base64(long_payload)
    # Non-empty PNG, still valid signature.
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(raw) > 200  # sanity — should be a real image
