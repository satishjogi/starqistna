"""Tests for _issue_gohub_tickets — the Stripe → CTS QR wiring.

Runs entirely offline. We monkey-patch `db` and `gohub_client.GoHubClient`
to isolate the wiring logic.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

import server  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory mongo stub
# ---------------------------------------------------------------------------

class _FakeColl:
    def __init__(self, docs: list[dict] | None = None):
        self.docs = docs or []
        self.updates: list[tuple[dict, dict]] = []

    async def find_one(self, filt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, filt, update):
        self.updates.append((filt, update))
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                if "$set" in update:
                    d.update(update["$set"])
                if "$unset" in update:
                    for k in update["$unset"]:
                        d.pop(k, None)
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class _FakeDB:
    def __init__(self):
        self.bookings = _FakeColl()
        self.schedules = _FakeColl()
        self.terminals = _FakeColl()


def _bookings_setup(booking_id: str = "BK1", ref: str = "SQCAFE1234") -> _FakeDB:
    fdb = _FakeDB()
    fdb.bookings.docs.append({
        "id": booking_id, "reference": ref,
        "schedule_id": "SCH1", "from_terminal_id": "T1", "to_terminal_id": "T2",
        "contact_phone": "0123456789",
        "passengers": [
            {"name": "Ali", "category": "adult", "ic_or_passport": "900101-14-5555", "seat_number": "1A"},
            {"name": "Ali Jr", "category": "child", "seat_number": "1B"},
        ],
        "status": "confirmed", "payment_status": "paid",
    })
    fdb.schedules.docs.append({
        "id": "SCH1", "trip_no": "SQ001",
        "departure_date": "2026-08-10", "departure_time": "11:30",
        "adult_fare": 55.0, "child_fare": 27.5,
    })
    fdb.terminals.docs.append({"id": "T1", "name": "TBS", "cts_code": "TBS"})
    fdb.terminals.docs.append({"id": "T2", "name": "GMC", "cts_code": "GMC"})
    return fdb


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_issue_skips_when_gohub_disabled(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "false")
    fdb = _bookings_setup()
    monkeypatch.setattr(server, "db", fdb)
    _run(server._issue_gohub_tickets("BK1"))
    doc = fdb.bookings.docs[0]
    assert doc["gohub_status"] == "skipped"
    assert "GOHUB_ENABLED=false" in doc["gohub_skip_reason"]


def test_issue_skips_when_missing_cts_code(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    fdb.terminals.docs[1].pop("cts_code")  # to_terminal has no code
    monkeypatch.setattr(server, "db", fdb)
    _run(server._issue_gohub_tickets("BK1"))
    assert fdb.bookings.docs[0]["gohub_status"] == "skipped"
    assert "to_terminal.cts_code missing" in fdb.bookings.docs[0]["gohub_skip_reason"]


def test_issue_skips_when_missing_trip_no(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    fdb.schedules.docs[0]["trip_no"] = ""
    monkeypatch.setattr(server, "db", fdb)
    _run(server._issue_gohub_tickets("BK1"))
    assert fdb.bookings.docs[0]["gohub_status"] == "skipped"


def test_issue_records_failure_on_gohub_error(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    monkeypatch.setattr(server, "db", fdb)

    from gohub_client import GoHubError

    class _FakeClient:
        def __init__(self, **_): pass
        async def get_qr(self, **_):
            raise GoHubError("12", "Online QR rate cannot be found")

    monkeypatch.setattr("gohub_client.GoHubClient", _FakeClient)
    _run(server._issue_gohub_tickets("BK1"))

    doc = fdb.bookings.docs[0]
    assert doc["gohub_status"] == "failed"
    assert doc["gohub_error"]["code"] == "12"
    assert "rate" in doc["gohub_error"]["message"].lower()


def test_issue_success_persists_tickets(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    monkeypatch.setattr(server, "db", fdb)

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **_): pass
        async def get_qr(self, **kwargs):
            captured.update(kwargs)
            return {
                "status_code": "0", "status_msg": "OK",
                "detail_opetickno": "SQ-SQCAFE12-1A",
                "detail_tickno": "CTS123456",
                "detail_QR": "OQR,QISTINA,cts...,ope...,hash",
            }
        async def fetch_qr_image(self, qr_value):
            # Return a valid-looking PNG so the ticket entry gets a qr_image data URL.
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    monkeypatch.setattr("gohub_client.GoHubClient", _FakeClient)
    _run(server._issue_gohub_tickets("BK1"))

    doc = fdb.bookings.docs[0]
    assert doc["gohub_status"] == "confirmed"
    assert doc.get("gohub_error") is None
    assert len(doc["gohub_tickets"]) == 2  # one per passenger
    assert doc["gohub_tickets"][0]["opetickno"].startswith("SQ-")
    assert doc["gohub_tickets"][0]["qr"].startswith("OQR,")
    # Branded image was fetched and persisted as a data URL.
    assert doc["gohub_tickets"][0]["qr_image"].startswith("data:image/png;base64,")
    assert doc["gohub_tickets"][1]["qr_image"].startswith("data:image/png;base64,")

    # Confirm the client got the right per-passenger seats.
    seats = captured["seats"]
    assert len(seats) == 2
    assert seats[0]["seattype"] == "A"       # first passenger is adult
    assert seats[1]["seattype"] == "C"       # second is child
    assert seats[0]["sprice"] == 55.0        # adult fare
    assert seats[1]["sprice"] == 27.5        # child fare
    assert captured["from_counter"] == "TBS"
    assert captured["to_counter"] == "GMC"
    assert captured["trip_no"] == "SQ001"


def test_issue_success_without_branded_image(monkeypatch):
    """When TBS's image endpoint fails, we still store the raw QR string
    and skip the qr_image field so the frontend falls back gracefully."""
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    monkeypatch.setattr(server, "db", fdb)

    class _FakeClient:
        def __init__(self, **_): pass
        async def get_qr(self, **_):
            return {
                "status_code": "0",
                "detail_QR": "OQR,QISTINA,cts,ope,hash",
                "detail_tickno": "CTS999",
            }
        async def fetch_qr_image(self, qr_value):
            return None   # simulate image endpoint failure

    monkeypatch.setattr("gohub_client.GoHubClient", _FakeClient)
    _run(server._issue_gohub_tickets("BK1"))

    doc = fdb.bookings.docs[0]
    assert doc["gohub_status"] == "confirmed"
    for t in doc["gohub_tickets"]:
        assert t["qr"].startswith("OQR,")
        assert "qr_image" not in t  # skipped when fetch returned None


def test_issue_never_raises_on_unexpected_exception(monkeypatch):
    """Never let an unexpected exception propagate back into the webhook path."""
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _bookings_setup()
    monkeypatch.setattr(server, "db", fdb)

    class _KaboomClient:
        def __init__(self, **_): pass
        async def get_qr(self, **_):
            raise RuntimeError("boom")

    monkeypatch.setattr("gohub_client.GoHubClient", _KaboomClient)
    _run(server._issue_gohub_tickets("BK1"))  # must NOT raise

    doc = fdb.bookings.docs[0]
    assert doc["gohub_status"] == "failed"
    assert doc["gohub_error"]["code"] == "UNEXPECTED"


def test_issue_noop_when_booking_missing(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    fdb = _FakeDB()  # no bookings
    monkeypatch.setattr(server, "db", fdb)
    _run(server._issue_gohub_tickets("BK_missing"))  # must not raise
