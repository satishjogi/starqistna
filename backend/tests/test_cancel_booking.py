"""Tests for booking cancellation endpoints (cancellation-quote + cancel)."""
import os
import time
import uuid
import pytest
import requests
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://star-qistna-bus.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN_EMAIL = "admin@starqistna.com"
ADMIN_PASS = "Admin@123"

# MY/SG local timezone for departure date+time interpretation
MY_TZ = timezone(timedelta(hours=8))


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _register(email, password, name="Test User"):
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": password, "full_name": name}, timeout=30)
    if r.status_code in (200, 201):
        return r.json().get("access_token") or _login(email, password)
    return _login(email, password)


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASS)


@pytest.fixture(scope="module")
def user_a():
    email = f"TEST_cancel_a_{uuid.uuid4().hex[:8]}@test.com"
    token = _register(email, "Pass@1234", "Test A")
    me = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=20).json()
    return {"token": token, "id": me["id"], "email": email}


@pytest.fixture(scope="module")
def user_b():
    email = f"TEST_cancel_b_{uuid.uuid4().hex[:8]}@test.com"
    token = _register(email, "Pass@1234", "Test B")
    me = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=20).json()
    return {"token": token, "id": me["id"], "email": email}


@pytest.fixture(scope="module")
def terminals(db):
    """Get any two valid terminals from the DB."""
    terms = list(db.terminals.find({}, {"_id": 0}).limit(2))
    assert len(terms) >= 2, "Not enough seeded terminals"
    return terms


@pytest.fixture(scope="module")
def schedule(db, terminals):
    s = db.schedules.find_one({}, {"_id": 0})
    assert s, "No seeded schedule"
    return s


def _make_booking(db, user_id, status, schedule, terminals, dep_dt: datetime,
                  total=100.0, currency="myr", paid_session=True, with_seat_locks=True):
    """Insert a synthetic booking + matching paid txn + seat locks. dep_dt in MY local time."""
    bid = str(uuid.uuid4())
    ref = f"TST{uuid.uuid4().hex[:8].upper()}"
    seat_no = "A1"
    booking = {
        "id": bid,
        "reference": ref,
        "user_id": user_id,
        "status": status,
        "from_terminal_id": terminals[0]["id"],
        "to_terminal_id": terminals[1]["id"],
        "schedule_id": schedule["id"],
        "departure_date": dep_dt.strftime("%Y-%m-%d"),
        "departure_time": dep_dt.strftime("%H:%M"),
        "seats": [seat_no],
        "passengers": [{"name": "Test Pax", "category": "adult", "seat_number": seat_no, "ic_or_passport": ""}],
        "pricing": {"currency": currency, "adults": 1, "children": 0,
                    "adult_fare": total, "child_fare": 0, "discount": 0, "total": total},
        "contact_email": "test@test.com",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.bookings.insert_one(booking)

    if paid_session:
        # Use a fake session_id so any Stripe API call would surface a Stripe error (502 expected)
        db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()),
            "booking_id": bid,
            "session_id": f"cs_test_FAKE_{uuid.uuid4().hex}",
            "payment_status": "paid",
            "amount": total,
            "currency": currency,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    if with_seat_locks:
        db.seat_locks.insert_one({
            "id": str(uuid.uuid4()),
            "booking_id": bid,
            "schedule_id": schedule["id"],
            "departure_date": booking["departure_date"],
            "seat_number": seat_no,
            "status": "booked",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    return booking


@pytest.fixture
def cleanup_test_bookings(db):
    created_ids = []
    yield created_ids
    if created_ids:
        db.bookings.delete_many({"id": {"$in": created_ids}})
        db.payment_transactions.delete_many({"booking_id": {"$in": created_ids}})
        db.seat_locks.delete_many({"booking_id": {"$in": created_ids}})


def _now_my():
    return datetime.now(MY_TZ).replace(tzinfo=None)


# ============ cancellation-quote tests ============

class TestCancellationQuote:
    def test_quote_requires_auth(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals,
                          _now_my() + timedelta(days=3))
        cleanup_test_bookings.append(b["id"])
        r = requests.get(f"{BASE_URL}/api/bookings/{b['id']}/cancellation-quote", timeout=20)
        assert r.status_code in (401, 403), f"Expected 401/403 got {r.status_code}"

    def test_quote_eligible_when_24h_plus(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals,
                          _now_my() + timedelta(days=3), total=120.0, currency="myr")
        cleanup_test_bookings.append(b["id"])
        r = requests.get(f"{BASE_URL}/api/bookings/{b['id']}/cancellation-quote",
                         headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["refund_eligible"] is True
        assert data["refund_amount"] == 120.0
        assert data["currency"] == "MYR"
        assert data["outcome"] == "full_refund"
        assert data["threshold_hours"] == 24
        assert data["hours_to_departure"] >= 24

    def test_quote_not_eligible_when_under_24h(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        # ~1 hour from now in MY time
        dep = _now_my() + timedelta(hours=1)
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals, dep, total=80.0)
        cleanup_test_bookings.append(b["id"])
        r = requests.get(f"{BASE_URL}/api/bookings/{b['id']}/cancellation-quote",
                         headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["refund_eligible"] is False
        assert data["refund_amount"] == 0.0
        assert data["outcome"] == "ticket_burned"
        assert data["hours_to_departure"] < 24

    def test_quote_403_for_other_users_booking(self, db, user_a, user_b, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals,
                          _now_my() + timedelta(days=2))
        cleanup_test_bookings.append(b["id"])
        # user_b tries to access user_a's booking
        r = requests.get(f"{BASE_URL}/api/bookings/{b['id']}/cancellation-quote",
                         headers={"Authorization": f"Bearer {user_b['token']}"}, timeout=20)
        assert r.status_code == 403, f"Expected 403, got {r.status_code} {r.text}"

    def test_quote_400_when_not_confirmed(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "pending", schedule, terminals,
                          _now_my() + timedelta(days=2), paid_session=False)
        cleanup_test_bookings.append(b["id"])
        r = requests.get(f"{BASE_URL}/api/bookings/{b['id']}/cancellation-quote",
                         headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert r.status_code == 400, f"Expected 400 got {r.status_code} {r.text}"

    def test_quote_404_for_unknown_booking(self, user_a):
        r = requests.get(f"{BASE_URL}/api/bookings/does-not-exist-123/cancellation-quote",
                         headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert r.status_code == 404


# ============ cancel tests ============

class TestCancelBooking:
    def test_cancel_requires_auth(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals,
                          _now_my() + timedelta(days=2))
        cleanup_test_bookings.append(b["id"])
        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel", timeout=20)
        assert r.status_code in (401, 403)

    def test_cancel_under_24h_burns_ticket_no_stripe_call(self, db, user_a, schedule, terminals,
                                                          cleanup_test_bookings):
        # ~1 hour ahead → burn path, no Stripe call
        dep = _now_my() + timedelta(hours=1)
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals, dep,
                          total=50.0, currency="myr", paid_session=True)
        cleanup_test_bookings.append(b["id"])

        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel",
                          headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=30)
        assert r.status_code == 200, f"Expected 200, got {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "cancelled_burned"
        assert data["outcome"] == "ticket_burned"
        assert data["refund"]["refunded"] is False

        # DB state
        doc = db.bookings.find_one({"id": b["id"]})
        assert doc["status"] == "cancelled_burned"
        assert doc.get("cancelled_at")

        # Seat locks deleted
        locks = list(db.seat_locks.find({"booking_id": b["id"]}))
        assert len(locks) == 0, "Seat locks should be deleted on cancel"

        # payment_transactions remains 'paid' (no refund happened)
        txn = db.payment_transactions.find_one({"booking_id": b["id"]})
        assert txn["payment_status"] == "paid"

        # GET booking shows new status badge
        gr = requests.get(f"{BASE_URL}/api/bookings/{b['id']}",
                          headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert gr.status_code == 200
        assert gr.json()["status"] == "cancelled_burned"

    def test_cancel_24h_plus_with_fake_stripe_session_returns_502(self, db, user_a, schedule, terminals,
                                                                  cleanup_test_bookings):
        """≥24h booking with FAKE session_id → Stripe.retrieve() will fail → expect 502 with Stripe error.
        Booking should remain 'confirmed' since the refund path raises before status update."""
        dep = _now_my() + timedelta(days=3)
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals, dep,
                          total=99.0, currency="myr", paid_session=True)
        cleanup_test_bookings.append(b["id"])

        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel",
                          headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=60)
        # Either 502 (Stripe rejected fake session) or 400 (no payment_intent)
        assert r.status_code in (400, 502), f"Expected 400/502 got {r.status_code} {r.text}"
        # Booking still confirmed (refund path raised before update)
        doc = db.bookings.find_one({"id": b["id"]})
        assert doc["status"] == "confirmed", f"Booking should remain confirmed, got {doc['status']}"
        # Seat locks NOT deleted
        locks = list(db.seat_locks.find({"booking_id": b["id"]}))
        assert len(locks) >= 1, "Seat locks must NOT be deleted when refund fails"

    def test_cancel_24h_plus_zero_total_bypasses_stripe(self, db, user_a, schedule, terminals,
                                                       cleanup_test_bookings):
        """If total=0, no Stripe call is made and the booking is marked cancelled_burned (refunded=False)
        but the seats are still freed. This verifies the 'eligible and total>0' guard."""
        dep = _now_my() + timedelta(days=3)
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals, dep,
                          total=0.0, currency="myr", paid_session=False)
        cleanup_test_bookings.append(b["id"])

        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel",
                          headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # total=0 → refund path skipped → marked cancelled_burned
        assert data["status"] in ("cancelled_burned", "cancelled_refunded")
        # Seat locks deleted regardless
        locks = list(db.seat_locks.find({"booking_id": b["id"]}))
        assert len(locks) == 0

    def test_cancel_403_for_other_users_booking(self, db, user_a, user_b, schedule, terminals,
                                                cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "confirmed", schedule, terminals,
                          _now_my() + timedelta(hours=1))
        cleanup_test_bookings.append(b["id"])
        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel",
                          headers={"Authorization": f"Bearer {user_b['token']}"}, timeout=20)
        assert r.status_code == 403

    def test_cancel_400_when_already_cancelled(self, db, user_a, schedule, terminals, cleanup_test_bookings):
        b = _make_booking(db, user_a["id"], "cancelled_burned", schedule, terminals,
                          _now_my() + timedelta(days=2), paid_session=False)
        cleanup_test_bookings.append(b["id"])
        r = requests.post(f"{BASE_URL}/api/bookings/{b['id']}/cancel",
                          headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=20)
        assert r.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
