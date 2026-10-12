"""Offline regression tests for Stripe booking finalization idempotency."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

import server  # noqa: E402


class _FakeCollection:
    def __init__(self, docs=None, fail_completion_once=False):
        self.docs = docs or []
        self.fail_completion_once = fail_completion_once

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(_FakeCollection._matches(document, clause) for clause in expected):
                    return False
                continue
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, projection=None):
        return next((doc for doc in self.docs if self._matches(doc, query)), None)

    async def update_one(self, query, update):
        if (
            self.fail_completion_once
            and update.get("$set", {}).get("booking_finalized") is True
        ):
            self.fail_completion_once = False
            raise RuntimeError("simulated completion write failure")

        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            for key, value in update.get("$set", {}).items():
                doc[key] = value
            for key in update.get("$unset", {}):
                doc.pop(key, None)
            for key, value in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + value
            for key, value in update.get("$addToSet", {}).items():
                doc.setdefault(key, [])
                if value not in doc[key]:
                    doc[key].append(value)
            return type("UpdateResult", (), {"modified_count": 1})()
        return type("UpdateResult", (), {"modified_count": 0})()

    async def update_many(self, query, update):
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(update.get("$set", {}))


class _FakeDB:
    def __init__(self, fail_completion_once=False):
        self.payment_transactions = _FakeCollection(
            [{"session_id": "cs_test", "booking_id": "booking1"}],
            fail_completion_once=fail_completion_once,
        )
        self.bookings = _FakeCollection([{
            "id": "booking1",
            "pricing": {"promo": {"code": "SAVE10"}},
        }])
        self.seat_locks = _FakeCollection([{
            "booking_id": "booking1",
            "status": "locked",
        }])
        self.promo_codes = _FakeCollection([{
            "code": "SAVE10",
            "used_count": 0,
        }])


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("concurrent", [False, True])
def test_duplicate_finalization_counts_promo_once(monkeypatch, concurrent):
    fake_db = _FakeDB()
    monkeypatch.setattr(server, "db", fake_db)

    async def no_op_issue(_booking_id):
        return None

    monkeypatch.setattr(server, "_issue_gohub_tickets", no_op_issue)

    async def finalize_twice():
        if concurrent:
            await asyncio.gather(
                server._finalize_booking("booking1", "cs_test"),
                server._finalize_booking("booking1", "cs_test"),
            )
        else:
            await server._finalize_booking("booking1", "cs_test")
            await server._finalize_booking("booking1", "cs_test")
        await asyncio.sleep(0)

    _run(finalize_twice())

    txn = fake_db.payment_transactions.docs[0]
    assert txn["booking_finalized"] is True
    assert txn["finalization_status"] == "complete"
    assert fake_db.promo_codes.docs[0]["used_count"] == 1
    assert fake_db.promo_codes.docs[0]["redemption_booking_ids"] == ["booking1"]
    assert fake_db.bookings.docs[0]["status"] == "confirmed"
    assert fake_db.seat_locks.docs[0]["status"] == "booked"


def test_retry_after_completion_write_failure_does_not_double_count_promo(monkeypatch):
    fake_db = _FakeDB(fail_completion_once=True)
    monkeypatch.setattr(server, "db", fake_db)

    async def no_op_issue(_booking_id):
        return None

    monkeypatch.setattr(server, "_issue_gohub_tickets", no_op_issue)

    async def retry():
        with pytest.raises(RuntimeError, match="simulated completion write failure"):
            await server._finalize_booking("booking1", "cs_test")
        await server._finalize_booking("booking1", "cs_test")
        await asyncio.sleep(0)

    _run(retry())

    assert fake_db.payment_transactions.docs[0]["booking_finalized"] is True
    assert fake_db.promo_codes.docs[0]["used_count"] == 1
