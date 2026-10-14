"""Phase 2 offline regression tests: single payment attempt, duplicate paid
sessions, stale finalization claims, webhook signatures and refund retries."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
SECRET = "whsec_phase2_test"
os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET

import server  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Res:
    def __init__(self, n):
        self.modified_count = n
        self.matched_count = n
        self.deleted_count = n


class _Coll:
    """Async in-memory collection; `unique_active` emulates the partial unique
    index on booking_id for rows with active=True."""

    def __init__(self, docs=None, unique_active=False):
        self.docs = docs or []
        self.unique_active = unique_active

    @classmethod
    def _match(cls, doc, query):
        for k, exp in query.items():
            if k == "$or":
                if not any(cls._match(doc, c) for c in exp):
                    return False
                continue
            actual = doc.get(k)
            if isinstance(exp, dict):
                for op, val in exp.items():
                    if op == "$ne" and (actual == val or (isinstance(actual, list) and val in actual)):
                        return False
                    if op == "$in" and actual not in val:
                        return False
                    if op == "$lt" and (actual is None or actual >= val):
                        return False
                    if op == "$exists" and (k in doc) != val:
                        return False
            elif actual != exp:
                return False
        return True

    async def find_one(self, query, projection=None, sort=None):
        hits = [d for d in self.docs if self._match(d, query)]
        if sort and hits:
            hits.sort(key=lambda d: d.get(sort[0][0], ""), reverse=sort[0][1] < 0)
        return dict(hits[0]) if hits else None

    async def insert_one(self, doc):
        if self.unique_active and doc.get("active") is True and any(
            d.get("active") is True and d["booking_id"] == doc["booking_id"] for d in self.docs
        ):
            raise DuplicateKeyError("dup active attempt")
        self.docs.append(dict(doc))

    async def update_one(self, query, update):
        for d in self.docs:
            if self._match(d, query):
                d.update(update.get("$set", {}))
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                for k, v in update.get("$addToSet", {}).items():
                    d.setdefault(k, [])
                    if v not in d[k]:
                        d[k].append(v)
                return _Res(1)
        return _Res(0)

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if self._match(d, query):
                d.update(update.get("$set", {}))
                n += 1
        return _Res(n)

    async def delete_one(self, query):
        for d in self.docs:
            if self._match(d, query):
                self.docs.remove(d)
                return _Res(1)
        return _Res(0)

    async def delete_many(self, query):
        keep = [d for d in self.docs if not self._match(d, query)]
        n = len(self.docs) - len(keep)
        self.docs[:] = keep
        return _Res(n)


class _DB:
    def __init__(self):
        self.bookings = _Coll([{
            "id": "b1", "reference": "SQ1", "user_id": "u1", "contact_email": "a@example.com",
            "payment_status": "pending", "status": "pending_payment",
            "pricing": {"total": 20.0, "currency": "myr", "promo": None},
        }])
        self.payment_transactions = _Coll(unique_active=True)
        self.seat_locks = _Coll([{"booking_id": "b1", "status": "locked"}])
        self.promo_codes = _Coll()


class _FakeStripe:
    created = 0

    def __init__(self, *a, **k):
        pass

    async def create_checkout_session(self, req):
        await asyncio.sleep(0)
        type(self).created += 1
        n = type(self).created
        return type("S", (), {"session_id": f"cs_{n}", "url": f"https://stripe.test/pay/cs_{n}"})()


@pytest.fixture
def env(monkeypatch):
    db = _DB()
    _FakeStripe.created = 0
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(server, "StripeCheckout", _FakeStripe)
    monkeypatch.setattr(server, "CheckoutSessionRequest", lambda **k: k)
    monkeypatch.setattr(server, "_payment_options_for", lambda c: [
        {"id": "card", "available": True}, {"id": "grabpay", "available": True}])
    issued = []

    async def fake_issue(booking_id):
        issued.append(booking_id)

    monkeypatch.setattr(server, "_issue_gohub_tickets", fake_issue)
    db.issued = issued
    return db


class _Req:
    base_url = "http://test/"
    client = None
    headers = {}


def _checkout(gateway="card"):
    body = server.CheckoutBody(booking_id="b1", origin_url="http://app", gateway=gateway)
    return server.create_checkout(body, _Req(), None)


# ---------- single active payment attempt ----------
def test_repeated_checkout_reuses_same_session(env):
    first = _run(_checkout())
    second = _run(_checkout())
    assert first == second
    assert _FakeStripe.created == 1
    assert len(env.payment_transactions.docs) == 1


def test_concurrent_checkout_creates_one_session(env):
    async def go():
        return await asyncio.gather(*[_checkout() for _ in range(5)], return_exceptions=True)

    results = _run(go())
    ok = [r for r in results if isinstance(r, dict)]
    assert _FakeStripe.created == 1
    assert {r["session_id"] for r in ok} == {"cs_1"}
    assert all(isinstance(r, (dict, HTTPException)) for r in results)
    assert len([d for d in env.payment_transactions.docs if d.get("active")]) == 1


def test_checkout_with_other_gateway_supersedes_previous(env):
    _run(_checkout("card"))
    second = _run(_checkout("grabpay"))
    assert second["session_id"] == "cs_2"
    active = [d for d in env.payment_transactions.docs if d.get("active")]
    assert [d["session_id"] for d in active] == ["cs_2"]


def test_checkout_stripe_failure_releases_claim(env, monkeypatch):
    class Boom(_FakeStripe):
        async def create_checkout_session(self, req):
            raise RuntimeError("stripe down")

    monkeypatch.setattr(server, "StripeCheckout", Boom)
    with pytest.raises(HTTPException) as e:
        _run(_checkout())
    assert e.value.status_code == 502
    assert env.payment_transactions.docs == []


def test_stale_placeholder_is_replaced(env):
    old = (server.utcnow() - timedelta(minutes=10)).isoformat()
    env.payment_transactions.docs.append({
        "session_id": "pending:x", "booking_id": "b1", "active": True, "created_at": old})
    assert _run(_checkout())["session_id"] == "cs_1"


def test_fresh_placeholder_returns_409(env):
    env.payment_transactions.docs.append({
        "session_id": "pending:x", "booking_id": "b1", "active": True,
        "created_at": server.utcnow().isoformat()})
    with pytest.raises(HTTPException) as e:
        _run(_checkout())
    assert e.value.status_code == 409


# ---------- two paid sessions for one booking ----------
def _txn(sid, **kw):
    return {"session_id": sid, "booking_id": "b1", "payment_status": "paid", "created_at": sid, **kw}


def test_second_paid_session_is_flagged_and_issues_no_second_ticket(env):
    env.payment_transactions.docs += [_txn("cs_a"), _txn("cs_b")]

    async def go():
        await server._finalize_booking("b1", "cs_a")
        await server._finalize_booking("b1", "cs_b")
        await asyncio.sleep(0)

    _run(go())
    a, b = env.payment_transactions.docs
    assert a["finalization_status"] == "complete"
    assert b["finalization_status"] == "duplicate_payment" and b["needs_refund_review"] is True
    assert env.bookings.docs[0]["paid_session_id"] == "cs_a"
    assert env.issued == ["b1"]


def test_concurrent_distinct_paid_sessions_issue_one_ticket(env):
    env.payment_transactions.docs += [_txn("cs_a"), _txn("cs_b")]

    async def go():
        await asyncio.gather(server._finalize_booking("b1", "cs_a"), server._finalize_booking("b1", "cs_b"))
        await asyncio.sleep(0)

    _run(go())
    assert env.issued == ["b1"]
    assert sorted(d["finalization_status"] for d in env.payment_transactions.docs) == ["complete", "duplicate_payment"]


def test_stale_finalization_claim_is_recovered(env):
    stale = (server.utcnow() - timedelta(seconds=server.FINALIZATION_LOCK_TTL_SECONDS + 5)).isoformat()
    env.payment_transactions.docs.append(_txn(
        "cs_a", finalization_status="processing", finalization_started_at=stale))
    _run(server._finalize_booking("b1", "cs_a"))
    assert env.payment_transactions.docs[0]["finalization_status"] == "complete"
    assert env.bookings.docs[0]["status"] == "confirmed"


def test_fresh_finalization_claim_is_respected(env):
    env.payment_transactions.docs.append(_txn(
        "cs_a", finalization_status="processing", finalization_started_at=server.utcnow().isoformat()))
    _run(server._finalize_booking("b1", "cs_a"))
    assert env.bookings.docs[0]["status"] == "pending_payment"


def test_refund_ignores_duplicate_payment_txn(env):
    env.payment_transactions.docs += [
        _txn("cs_a"), _txn("cs_b", duplicate_payment=True)]
    found = _run(env.payment_transactions.find_one(
        {"booking_id": "b1", "payment_status": "paid", "duplicate_payment": {"$ne": True}},
        sort=[("created_at", -1)]))
    assert found["session_id"] == "cs_a"


# ---------- webhook signatures ----------
client = TestClient(server.app)


def _sig(payload, secret=SECRET, ts=None):
    ts = str(ts or int(time.time()))
    v1 = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def _post(payload, sig=None):
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if sig:
        headers["Stripe-Signature"] = sig
    return client.post("/api/webhook/stripe", content=body, headers=headers), body


EVENT = {"object": "event", "id": "evt_1", "type": "checkout.session.completed",
         "data": {"object": {"id": "cs_a", "payment_status": "paid"}}}


def test_webhook_missing_wrong_secret_and_stale_signature_rejected(env):
    env.payment_transactions.docs.append(_txn("cs_a", payment_status="initiated"))
    r, _ = _post(EVENT)
    assert r.status_code == 400 and r.json()["error"] == "missing_signature"
    body = json.dumps(EVENT)
    r = client.post("/api/webhook/stripe", content=body, headers={"Stripe-Signature": _sig(body, "whsec_other")})
    assert r.status_code == 400
    r = client.post("/api/webhook/stripe", content=body,
                    headers={"Stripe-Signature": _sig(body, ts=int(time.time()) - 3600)})
    assert r.status_code == 400
    assert env.bookings.docs[0]["status"] == "pending_payment"


def test_webhook_replay_finalizes_once(env):
    env.payment_transactions.docs.append(_txn("cs_a", payment_status="initiated"))
    body = json.dumps(EVENT)
    for _ in range(3):
        r = client.post("/api/webhook/stripe", content=body, headers={"Stripe-Signature": _sig(body)})
        assert r.status_code == 200
    assert env.bookings.docs[0]["status"] == "confirmed"
    assert env.payment_transactions.docs[0]["finalization_status"] == "complete"


# ---------- cancellation / refund retry ----------
def test_cancel_refund_retry_reuses_idempotency_key(env, monkeypatch):
    dep = (server.utcnow() + timedelta(days=5))
    env.bookings.docs[0].update({
        "status": "confirmed", "payment_status": "paid",
        "departure_date": dep.strftime("%Y-%m-%d"), "departure_time": "12:00"})
    env.payment_transactions.docs.append(_txn("cs_a", amount=20.0, currency="myr"))
    keys, calls = [], {"n": 0}

    def fake_refund(pi, amount, idem):
        keys.append(idem)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network")
        return {"id": "re_1"}

    monkeypatch.setattr(server, "_stripe_get_payment_intent_sync", lambda sid: "pi_1")
    monkeypatch.setattr(server, "_stripe_refund_sync", fake_refund)
    with pytest.raises(HTTPException) as e:
        _run(server.cancel_booking("b1", {"id": "u1"}))
    assert e.value.status_code == 502
    assert env.bookings.docs[0]["status"] == "confirmed"
    assert env.seat_locks.docs  # seats not released on failed refund
    out = _run(server.cancel_booking("b1", {"id": "u1"}))
    assert out["refund"]["refunded"] is True
    assert keys == ["refund:b1", "refund:b1"]
