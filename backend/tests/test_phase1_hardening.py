"""Offline Phase 1 hardening regression tests (seat locks, boarding, payment status, index, webhook)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

import server  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Coll:
    """Minimal async collection supporting the operators the hardened paths use."""

    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _match(doc, query):
        for k, exp in query.items():
            actual = doc.get(k)
            if isinstance(exp, dict):
                for op, val in exp.items():
                    if op == "$in" and actual not in val:
                        return False
                    if op == "$gt" and not (actual is not None and actual > val):
                        return False
                    if op == "$exists" and (k in doc) != val:
                        return False
            elif actual != exp:
                return False
        return True

    async def find_one(self, query, projection=None):
        return next((dict(d) for d in self.docs if self._match(d, query)), None)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if self._match(d, query):
                d.update(update.get("$set", {}))
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                n += 1
        return type("R", (), {"modified_count": n})()

    async def update_one(self, query, update):
        for d in self.docs:
            if self._match(d, query):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class _DB:
    def __init__(self, seats=("1A", "1B")):
        self.schedules = _Coll([{
            "id": "s1", "departure_date": "2030-01-01", "departure_time": "08:00",
            "from_terminal_id": "a", "to_terminal_id": "b", "adult_fare": 10, "currency": "myr",
        }])
        future = "2999-01-01T00:00:00+00:00"
        self.seat_locks = _Coll([
            {"id": f"l{i}", "schedule_id": "s1", "seat_number": sn, "status": "locked",
             "owner": "guest:tok", "lock_token": "tok", "expires_at": future}
            for i, sn in enumerate(seats)
        ])
        self.bookings = _Coll()
        self.promo_codes = _Coll()


def _body(**over):
    data = dict(
        schedule_id="s1",
        seat_assignments=[{"seat_number": "1A", "passenger_index": 0}],
        passengers=[{"name": "A", "category": "adult"}],
        contact_email="a@example.com", contact_phone="+60123456789", lock_token="tok",
    )
    data.update(over)
    return server.CreateBookingBody(**data)


def test_booking_requires_lock_token(monkeypatch):
    monkeypatch.setattr(server, "db", _DB())
    with pytest.raises(HTTPException) as e:
        _run(server.create_booking(_body(lock_token=None), None))
    assert e.value.status_code == 400


def test_booking_rejects_wrong_token_and_wrong_owner(monkeypatch):
    db = _DB()
    monkeypatch.setattr(server, "db", db)
    with pytest.raises(HTTPException) as e:
        _run(server.create_booking(_body(lock_token="other"), None))
    assert e.value.status_code == 409
    db.seat_locks.docs[0]["owner"] = "user-1"
    with pytest.raises(HTTPException) as e:
        _run(server.create_booking(_body(), {"id": "user-2"}))
    assert e.value.status_code == 409
    assert not db.bookings.docs


def test_booking_rejects_expired_lock(monkeypatch):
    db = _DB()
    db.seat_locks.docs[0]["expires_at"] = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(server, "db", db)
    with pytest.raises(HTTPException) as e:
        _run(server.create_booking(_body(), None))
    assert e.value.status_code == 409


def test_lock_cannot_be_reused_across_bookings(monkeypatch):
    db = _DB()
    monkeypatch.setattr(server, "db", db)
    first = _run(server.create_booking(_body(), None))
    assert db.seat_locks.docs[0]["booking_id"] == first["id"]
    with pytest.raises(HTTPException) as e:
        _run(server.create_booking(_body(), None))
    assert e.value.status_code == 409
    assert len(db.bookings.docs) == 1


def test_concurrent_booking_attempts_only_one_wins(monkeypatch):
    db = _DB()
    monkeypatch.setattr(server, "db", db)

    async def go():
        return await asyncio.gather(
            *[server.create_booking(_body(), None) for _ in range(5)], return_exceptions=True
        )

    results = _run(go())
    wins = [r for r in results if isinstance(r, dict)]
    assert len(wins) == 1
    assert len(db.bookings.docs) == 1


# ---------- payment status ----------
def _txn(token="rcpt"):
    return {"session_id": "cs1", "booking_id": "b1", "user_id": "u1",
            "payment_status": "paid", "status": "complete", "booking_finalized": True,
            "receipt_token_hash": hashlib.sha256(token.encode()).hexdigest()}


class _PayDB:
    def __init__(self):
        self.payment_transactions = _Coll([_txn()])
        self.bookings = _Coll([{"id": "b1", "contact_email": "secret@example.com", "passengers": [{"name": "X"}]}])


class _Req:
    base_url = "http://test/"


def test_payment_status_hides_booking_from_session_id_alone(monkeypatch):
    monkeypatch.setattr(server, "db", _PayDB())
    r = _run(server.payment_status("cs1", _Req(), receipt=None, user=None))
    assert r == {"payment_status": "paid", "status": "complete"}
    r = _run(server.payment_status("cs1", _Req(), receipt="wrong", user=None))
    assert "booking" not in r
    r = _run(server.payment_status("cs1", _Req(), receipt=None, user={"id": "someone-else"}))
    assert "booking" not in r


def test_payment_status_returns_booking_with_receipt_owner_or_admin(monkeypatch):
    monkeypatch.setattr(server, "db", _PayDB())
    assert _run(server.payment_status("cs1", _Req(), receipt="rcpt", user=None))["booking"]["id"] == "b1"
    assert "booking" in _run(server.payment_status("cs1", _Req(), receipt=None, user={"id": "u1"}))
    assert "booking" in _run(server.payment_status("cs1", _Req(), receipt=None, user={"id": "z", "is_admin": True}))


# ---------- boarding ----------
def test_boarding_requires_scanner_role():
    with pytest.raises(HTTPException) as e:
        _run(server.require_scanner({"id": "u", "is_admin": False}))
    assert e.value.status_code == 403
    assert _run(server.require_scanner({"id": "u", "role": "gate_scanner"}))
    assert _run(server.require_scanner({"id": "a", "is_admin": True}))


def test_boarding_route_rejects_anonymous():
    r = TestClient(server.app).post("/api/boarding/validate", json={"reference": "SQ12345678"})
    assert r.status_code in (401, 403)


def test_boarding_minimal_response_and_single_board(monkeypatch):
    db = type("D", (), {})()
    db.bookings = _Coll([{
        "id": "b1", "reference": "SQ1", "status": "confirmed", "schedule_id": "s1",
        "contact_email": "secret@example.com", "contact_phone": "+60",
        "passengers": [{"name": "A", "category": "adult", "seat_number": "1A", "ic_or_passport": "S123"}],
        "seats": ["1A"], "from_terminal_id": "a", "to_terminal_id": "b",
        "departure_date": "d", "departure_time": "t",
    }])
    monkeypatch.setattr(server, "db", db)

    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(server, "log_audit", no_audit)
    monkeypatch.setattr(server, "BOARDING_API_KEY", "")
    body = server.BoardingValidateBody(reference="sq1", gate="G1")
    first = _run(server.boarding_validate(body, None, {"id": "g"}))
    assert first["valid"] and not first["already_boarded"]
    assert "contact_email" not in first and "schedule" not in first
    assert "ic_or_passport" not in first["passengers"][0]
    second = _run(server.boarding_validate(body, None, {"id": "g"}))
    assert second["already_boarded"]


# ---------- webhook + index ----------
def test_webhook_rejects_when_secret_unset(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "")
    r = TestClient(server.app).post(
        "/api/webhook/stripe", content=json.dumps({"type": "checkout.session.completed"})
    )
    assert r.status_code == 503


def test_index_failure_fails_closed(monkeypatch):
    class _Idx:
        async def create_index(self, *a, **k):
            raise DuplicateKeyError("dup")

        async def drop_index(self, *a, **k):
            return None

    db = type("D", (), {"schedules": _Idx(), "seat_locks": _Idx()})()
    monkeypatch.setattr(server, "db", db)
    with pytest.raises(RuntimeError):
        _run(server._ensure_indexes())


# ---------- Phase 3 hardening ----------
def test_client_ip_ignores_forwarded_header_unless_trusted(monkeypatch):
    req = type("R", (), {"headers": {"x-forwarded-for": "6.6.6.6, 10.0.0.1"},
                         "client": type("C", (), {"host": "9.9.9.9"})()})()
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    assert server._client_ip(req) == "9.9.9.9"
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    assert server._client_ip(req) == "10.0.0.1"


def test_ready_reports_503_when_db_down(monkeypatch):
    class _D:
        async def command(self, *a):
            raise RuntimeError("down")

    monkeypatch.setattr(server, "db", _D())
    assert _run(server.ready()).status_code == 503


def test_email_escapes_passenger_name():
    import email_service
    assert email_service._esc("<script>") == "&lt;script&gt;"
