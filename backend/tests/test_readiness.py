"""Unit tests for GET /api/ready (leg-level seat index readiness)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "starqistna_ready_test")
os.environ.setdefault("JWT_SECRET", "ready-test-secret")
os.environ.setdefault("STRIPE_API_KEY", "sk_test_ready")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "ready-test-password")

import server  # noqa: E402


class _Coll:
    def __init__(self, names=None, exc=None):
        self.names, self.exc = names or [], exc

    async def index_information(self):
        if self.exc:
            raise self.exc
        return {n: {} for n in self.names}


class _DB:
    def __init__(self, coll=None, ping_exc=None, with_coll=True):
        self.ping_exc = ping_exc
        if with_coll:
            self.seat_leg_locks = coll

    async def command(self, name):
        if self.ping_exc:
            raise self.ping_exc
        return {"ok": 1}


def _call(monkeypatch, db):
    monkeypatch.setattr(server, "db", db)
    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(server.ready())
    finally:
        loop.close()
    if isinstance(res, dict):
        return 200, res
    return res.status_code, json.loads(res.body)


def test_ready_ok(monkeypatch):
    code, body = _call(monkeypatch, _DB(_Coll(["_id_", *server.REQUIRED_SEAT_LEG_INDEXES])))
    assert code == 200 and body["status"] == "ready"


def test_missing_leg_index(monkeypatch):
    code, body = _call(monkeypatch, _DB(_Coll(["_id_", "uniq_seat_lock_leg"])))
    assert code == 503 and body["reason"] == "seat_index_missing"


def test_legacy_index_only_not_ready(monkeypatch):
    code, body = _call(monkeypatch, _DB(_Coll(["uniq_bus_instance_seat_active"])))
    assert code == 503 and body["reason"] == "seat_index_missing"


def test_collection_unavailable(monkeypatch):
    code, body = _call(monkeypatch, _DB(None))
    assert code == 503 and body["reason"] == "seat_index_missing"
    code, body = _call(monkeypatch, _DB(with_coll=False))
    assert code == 503


def test_ping_failure(monkeypatch):
    code, body = _call(monkeypatch, _DB(_Coll(), ping_exc=RuntimeError("down")))
    assert code == 503 and body == {"status": "unavailable"}


def test_index_information_failure(monkeypatch):
    code, body = _call(monkeypatch, _DB(_Coll(exc=RuntimeError("boom"))))
    assert code == 503 and body == {"status": "unavailable"}
