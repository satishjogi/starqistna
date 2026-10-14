"""Real-MongoDB integration tests. Skipped unless MONGO_TEST_URL is set, e.g.

    MONGO_TEST_URL=mongodb://localhost:27017 pytest backend/tests/test_mongo_integration.py

Uses a throwaway database that is dropped after each test.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

MONGO_TEST_URL = os.environ.get("MONGO_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(not MONGO_TEST_URL, reason="MONGO_TEST_URL not set")

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
if MONGO_TEST_URL:
    os.environ["MONGO_URL"] = MONGO_TEST_URL
    os.environ.setdefault("DB_NAME", "starqistna_it_bootstrap")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    os.environ.setdefault("STRIPE_API_KEY", "sk_test_x")
    os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "Str0ngPassw0rd!xyz")
    os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

    from fastapi import HTTPException  # noqa: E402
    from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
    import server  # noqa: E402


_LOOP = asyncio.new_event_loop()


def _run(coro):
    # One shared loop: Motor binds its client to a single event loop.
    return _LOOP.run_until_complete(coro)


@pytest.fixture
def mdb(monkeypatch):
    # Fresh client per test: each test uses its own event loop.
    client = AsyncIOMotorClient(MONGO_TEST_URL, io_loop=_LOOP)
    name = f"starqistna_it_{uuid.uuid4().hex[:8]}"
    db = client[name]
    monkeypatch.setattr(server, "db", db)
    yield db
    _run(client.drop_database(name))
    client.close()


async def _seed(db, n_sched=2):
    for i in range(n_sched):
        await db.schedules.insert_one({
            "id": f"s{i}", "bus_instance_id": "bus1", "departure_date": "2999-01-01",
            "departure_time": "08:00", "from_terminal_id": "a", "to_terminal_id": "b",
            "adult_fare": 10, "currency": "myr"})


def _lock(sched, seats, user=None):
    return server.lock_seats(server.SeatLockBody(schedule_id=sched, seat_numbers=seats), None, user)


def _book(sched, token, seat="1A"):
    return server.create_booking(server.CreateBookingBody(
        schedule_id=sched,
        seat_assignments=[{"seat_number": seat, "passenger_index": 0}],
        passengers=[{"name": "A", "category": "adult"}],
        contact_email="a@example.com", contact_phone="+60123456789", lock_token=token), None)


def test_seat_uniqueness_index_is_created(mdb):
    _run(server._ensure_indexes())
    names = _run(mdb.seat_locks.index_information())
    assert "uniq_bus_instance_seat_active" in names
    assert "uniq_active_payment_per_booking" in _run(mdb.payment_transactions.index_information())


def test_startup_fails_closed_when_legacy_duplicates_exist(mdb):
    async def go():
        for i in range(2):
            await mdb.seat_locks.insert_one({
                "id": str(i), "bus_instance_id": "bus1", "seat_number": "1A",
                "status": "locked", "schedule_id": f"s{i}"})
        await server._ensure_indexes()

    with pytest.raises(RuntimeError):
        _run(go())


def test_concurrent_locks_on_same_seat_one_wins(mdb):
    async def go():
        await server._ensure_indexes()
        await _seed(mdb)
        return await asyncio.gather(*[_lock("s0", ["1A"]) for _ in range(8)], return_exceptions=True)

    results = _run(go())
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(isinstance(r, (dict, HTTPException)) for r in results)


def test_sibling_schedules_share_seat_pool(mdb):
    async def go():
        await server._ensure_indexes()
        await _seed(mdb)
        return await asyncio.gather(_lock("s0", ["1A"]), _lock("s1", ["1A"]), return_exceptions=True)

    results = _run(go())
    assert sum(isinstance(r, dict) for r in results) == 1
    assert any(isinstance(r, HTTPException) and r.status_code == 409 for r in results)


def test_concurrent_bookings_reusing_one_lock_one_wins(mdb):
    async def go():
        await server._ensure_indexes()
        await _seed(mdb, 1)
        lock = await _lock("s0", ["1A"])
        return await asyncio.gather(
            *[_book("s0", lock["lock_token"]) for _ in range(8)], return_exceptions=True)

    results = _run(go())
    wins = [r for r in results if isinstance(r, dict)]
    losses = [r for r in results if isinstance(r, HTTPException)]
    assert len(wins) == 1
    assert len(losses) == 7 and all(e.status_code == 409 for e in losses)
    assert _run(mdb.bookings.count_documents({})) == 1
