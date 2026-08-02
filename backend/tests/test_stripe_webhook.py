"""
Stripe webhook resilience tests.

These exercise the /api/webhook/stripe endpoint directly through FastAPI's
TestClient so we don't need to reach out to Stripe. We use stripe_sdk to
craft a real signature for the "verified" path.
"""
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# Make backend importable and force STRIPE_WEBHOOK_SECRET before server import.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

TEST_SECRET = "whsec_test_dummy_secret_for_pytest_only"
os.environ["STRIPE_WEBHOOK_SECRET"] = TEST_SECRET

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402

# Ensure the module-level cached secret matches (in case server was imported earlier).
server.STRIPE_WEBHOOK_SECRET = TEST_SECRET

client = TestClient(server.app)


def _sign(payload: str, secret: str = TEST_SECRET) -> str:
    ts = str(int(time.time()))
    signed = f"{ts}.{payload}".encode("utf-8")
    v1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def _post(payload: dict, sign: bool = True, secret: str = TEST_SECRET):
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["Stripe-Signature"] = _sign(body, secret)
    return client.post("/api/webhook/stripe", data=body, headers=headers)


def test_missing_signature_returns_400():
    r = client.post(
        "/api/webhook/stripe",
        data=json.dumps({"type": "checkout.session.completed", "id": "evt_x"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "missing_signature"


def test_bad_signature_returns_400():
    body = json.dumps({"type": "checkout.session.completed", "id": "evt_x"})
    r = client.post(
        "/api/webhook/stripe",
        data=body,
        headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert r.status_code == 400
    assert r.json()["error"] in ("invalid_signature", "verification_error")


def test_valid_signature_unknown_session_returns_200():
    """Unknown session (no matching payment_transaction) must NOT 5xx."""
    payload = {
        "object": "event",
        "type": "checkout.session.completed",
        "id": "evt_test_1",
        "api_version": "2024-06-20",
        "data": {"object": {"id": "cs_missing_txn", "payment_status": "paid"}},
    }
    r = _post(payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_valid_signature_unknown_event_type_returns_200():
    payload = {
        "object": "event",
        "type": "customer.updated",
        "id": "evt_test_2",
        "api_version": "2024-06-20",
        "data": {"object": {"id": "cus_123"}},
    }
    r = _post(payload)
    assert r.status_code == 200
    assert r.json().get("ignored") == "customer.updated"


def test_expired_event_updates_txn_without_crash():
    payload = {
        "object": "event",
        "type": "checkout.session.expired",
        "id": "evt_test_3",
        "api_version": "2024-06-20",
        "data": {"object": {"id": "cs_expired_test", "payment_status": "unpaid"}},
    }
    r = _post(payload)
    assert r.status_code == 200


def test_payment_intent_failed_returns_200():
    payload = {
        "object": "event",
        "type": "payment_intent.payment_failed",
        "id": "evt_test_4",
        "api_version": "2024-06-20",
        "data": {"object": {"metadata": {"checkout_session_id": "cs_nonexistent"}}},
    }
    r = _post(payload)
    assert r.status_code == 200


def test_malformed_body_with_bad_sig_returns_400_not_500():
    r = client.post(
        "/api/webhook/stripe",
        data="not-json-at-all",
        headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=deadbeef"},
    )
    # Must be 4xx (client error), never 5xx.
    assert 400 <= r.status_code < 500
