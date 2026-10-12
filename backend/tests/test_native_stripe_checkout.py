"""Offline checks for native Stripe Checkout request/response handling."""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
for key, value in {
    "MONGO_URL": "mongodb://localhost:27017",
    "DB_NAME": "test_db",
    "JWT_SECRET": "test-secret",
    "STRIPE_API_KEY": "sk_test_dummy",
    "BOOTSTRAP_ADMIN_PASSWORD": "Test-Password-1",
}.items():
    os.environ.setdefault(key, value)

from routers import payments  # noqa: E402


class _Collection:
    def __init__(self, document=None):
        self.document = document
        self.inserted = None
        self.updated = None

    async def find_one(self, *_args, **_kwargs):
        return self.document

    async def insert_one(self, document):
        self.inserted = document

    async def update_one(self, query, update):
        self.updated = (query, update)


def test_checkout_uses_native_stripe_session_and_preserves_transaction(monkeypatch):
    bookings = _Collection({
        "id": "BK1",
        "reference": "SQ123",
        "payment_status": "unpaid",
        "pricing": {"currency": "myr", "total": 25.5},
        "contact_email": "guest@example.com",
    })
    transactions = _Collection()
    monkeypatch.setattr(payments, "db", SimpleNamespace(bookings=bookings, payment_transactions=transactions))
    create_session = AsyncMock(return_value=SimpleNamespace(id="cs_test_1", url="https://stripe.example/checkout"))
    monkeypatch.setattr(payments.stripe_sdk.checkout.Session, "create_async", create_session)

    result = asyncio.run(
        payments.create_checkout(
            payments.CheckoutBody(booking_id="BK1", origin_url="https://starqistna.com"),
            None,
        )
    )

    assert result == {"url": "https://stripe.example/checkout", "session_id": "cs_test_1"}
    params = create_session.await_args.kwargs
    assert params["mode"] == "payment"
    assert params["payment_method_types"] == ["card"]
    assert params["line_items"][0]["price_data"]["unit_amount"] == 2550
    assert params["line_items"][0]["price_data"]["currency"] == "myr"
    assert params["success_url"].endswith("session_id={CHECKOUT_SESSION_ID}&booking_id=BK1")
    assert params["metadata"]["booking_reference"] == "SQ123"
    assert transactions.inserted["session_id"] == "cs_test_1"
    assert transactions.inserted["payment_status"] == "initiated"


def test_payment_status_reads_native_stripe_session(monkeypatch):
    transactions = _Collection({
        "session_id": "cs_test_1",
        "booking_id": "BK1",
        "payment_status": "initiated",
        "status": "open",
    })
    bookings = _Collection({"id": "BK1", "payment_status": "unpaid"})
    monkeypatch.setattr(payments, "db", SimpleNamespace(bookings=bookings, payment_transactions=transactions))
    retrieve_session = AsyncMock(return_value=SimpleNamespace(status="complete", payment_status="unpaid"))
    monkeypatch.setattr(payments.stripe_sdk.checkout.Session, "retrieve_async", retrieve_session)

    result = asyncio.run(payments.payment_status("cs_test_1", None))

    assert result == {
        "payment_status": "unpaid",
        "status": "complete",
        "booking": {"id": "BK1", "payment_status": "unpaid"},
    }
    assert retrieve_session.await_args.args == ("cs_test_1",)
    updates = transactions.updated[1]["$set"]
    assert updates["status"] == "complete"
    assert updates["payment_status"] == "unpaid"
    assert updates["updated_at"]
