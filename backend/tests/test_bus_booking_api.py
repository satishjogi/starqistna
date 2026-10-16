"""MongoDB-backed booking API integration tests; Stripe is stubbed."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pymongo import ASCENDING, MongoClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MONGO_TEST_URL = os.environ.get("MONGO_TEST_URL")
if not MONGO_TEST_URL:
    pytest.skip("Set MONGO_TEST_URL to run booking API integration tests", allow_module_level=True)

os.environ["MONGO_URL"] = MONGO_TEST_URL
os.environ["DB_NAME"] = "starqistna_bus_booking_test"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "booking-api-test-secret")
os.environ.setdefault("STRIPE_API_KEY", "sk_test_booking_api")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_booking_api_test")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "booking-api-test-password")

import server  # noqa: E402


class _FakeStripe:
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def create_checkout_session(self, request):
        type(self).requests.append(request)
        number = len(type(self).requests)
        return type(
            "CheckoutSession",
            (),
            {
                "session_id": f"cs_test_{number}",
                "url": f"https://checkout.stripe.test/session/{number}",
            },
        )()


@pytest.fixture
def api(monkeypatch):
    database_name = f"starqistna_booking_test_{uuid.uuid4().hex}"
    mongo = MongoClient(MONGO_TEST_URL)
    sync_db = mongo[database_name]
    async_client = server.AsyncIOMotorClient(MONGO_TEST_URL)
    db = async_client[database_name]

    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "StripeCheckout", _FakeStripe)
    monkeypatch.setattr(server, "CheckoutSessionRequest", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        server,
        "_payment_options_for",
        lambda currency: [{"id": "card", "available": True}],
    )
    monkeypatch.setenv("CHECKOUT_ALLOWED_ORIGINS", "https://booking.test")
    _FakeStripe.requests = []

    async def no_throttle(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "_check_auth_throttle", no_throttle)
    monkeypatch.setattr(server, "_record_auth_failure", no_throttle)
    sync_db.seat_locks.create_index(
        [("bus_instance_id", ASCENDING), ("seat_number", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": {"$in": ["locked", "booked"]}},
    )
    sync_db.payment_transactions.create_index(
        [("booking_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"active": True},
    )
    sync_db.schedules.insert_one(
        {
            "id": "schedule-1",
            "bus_instance_id": "bus-1",
            "departure_date": "2030-01-01",
            "departure_time": "08:00",
            "from_terminal_id": "terminal-a",
            "to_terminal_id": "terminal-b",
            "adult_fare": 40,
            "child_fare": 20,
            "currency": "myr",
            "total_seats": 4,
            "layout_config": "2+2",
        }
    )

    client = TestClient(server.app)
    try:
        yield client, sync_db
    finally:
        client.close()
        mongo.drop_database(database_name)
        async_client.close()
        mongo.close()


def _lock_seat(client):
    response = client.post(
        "/api/seats/lock",
        json={"schedule_id": "schedule-1", "seat_numbers": ["1A"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["lock_token"]


def _create_booking(client, lock_token):
    return client.post(
        "/api/bookings",
        json={
            "schedule_id": "schedule-1",
            "seat_assignments": [{"seat_number": "1A", "passenger_index": 0}],
            "passengers": [
                {
                    "name": "Test Passenger",
                    "category": "adult",
                    "ic_or_passport": "TEST123",
                }
            ],
            "contact_email": "booking@example.com",
            "contact_phone": "+60123456789",
            "lock_token": lock_token,
        },
    )


def test_booking_requires_lock_token_and_checkout_authorization(api):
    client, _ = api
    lock_token = _lock_seat(client)

    invalid_booking = _create_booking(client, None)
    assert invalid_booking.status_code == 400
    booking_response = _create_booking(client, lock_token)
    assert booking_response.status_code == 200, booking_response.text
    booking = booking_response.json()
    assert booking["status"] == "pending_payment"
    assert booking["payment_status"] == "pending"
    assert booking["pricing"]["total"] == 40

    checkout_url = "/api/payments/checkout"
    unauthorized = client.post(
        checkout_url,
        json={"booking_id": booking["id"], "origin_url": "https://booking.test"},
    )
    assert unauthorized.status_code == 404
    assert _FakeStripe.requests == []

    checkout = client.post(
        checkout_url,
        json={
            "booking_id": booking["id"],
            "origin_url": "https://booking.test",
            "lock_token": lock_token,
        },
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json() == {
        "session_id": "cs_test_1",
        "url": "https://checkout.stripe.test/session/1",
    }
    assert _FakeStripe.requests[0]["metadata"]["booking_id"] == booking["id"]
    assert _FakeStripe.requests[0]["metadata"]["payment_attempt_id"]


def test_checkout_email_authorization_and_expired_hold_failure(api):
    client, sync_db = api
    lock_token = _lock_seat(client)
    booking_response = _create_booking(client, lock_token)
    assert booking_response.status_code == 200, booking_response.text
    booking_id = booking_response.json()["id"]

    checkout_url = "/api/payments/checkout"
    wrong_email = client.post(
        checkout_url,
        json={
            "booking_id": booking_id,
            "origin_url": "https://booking.test",
            "email": "not-the-booker@example.com",
        },
    )
    assert wrong_email.status_code == 404
    assert _FakeStripe.requests == []

    sync_db.seat_locks.update_many(
        {"booking_id": booking_id},
        {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}},
    )
    expired_hold = client.post(
        checkout_url,
        json={
            "booking_id": booking_id,
            "origin_url": "https://booking.test",
            "email": "BOOKING@example.com",
        },
    )
    assert expired_hold.status_code == 409
    assert _FakeStripe.requests == []
