"""
Day-1 security + performance audit regression tests for Star Qistna.

Covers:
  C1  JWT secret rotation -> old/forged tokens 401
  C2  CORS no Allow-Credentials header on /api/health
  C3  Bootstrap admin login still works (and password not in logs - checked
      separately via grep)
  C4  Booking ownership enforcement (owner / admin / guest-email / forbidden)
  H1  Regex-injection-safe terminal & audit-log queries
  H4  /api/payments/options/{booking_id} requires auth or matching email
  P-H2  /api/search shape + seats_available
  P-H5  /api/popular/now 30s TTL cache
  P-M1  /api/bookings/me populates from/to terminal objects
  P-M3  admin/stats + admin/feedback summary shape
  Regression  cancellation-quote auth/owner enforcement
"""
import os
import time
import uuid
import jwt as pyjwt
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get(
    "REACT_APP_BACKEND_URL"
) else "https://star-qistna-bus.preview.emergentagent.com"
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@starqistna.com"
ADMIN_PASSWORD = "Admin@123"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(session):
    r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"No token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def user_a(session):
    """A fresh registered, non-admin user."""
    suffix = uuid.uuid4().hex[:8]
    email = f"TEST_audit_a_{suffix}@test.com"
    pw = "PassA1234!"
    r = session.post(f"{API}/auth/register", json={"email": email, "password": pw, "full_name": "Test A", "name": "Test A"})
    assert r.status_code in (200, 201), f"register A: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if not tok:
        # Some impls require a separate login
        lr = session.post(f"{API}/auth/login", json={"email": email, "password": pw})
        tok = lr.json().get("access_token") or lr.json().get("token")
    user_id = body.get("user", {}).get("id") or body.get("id")
    return {"email": email, "password": pw, "token": tok, "id": user_id}


@pytest.fixture(scope="session")
def user_b(session):
    suffix = uuid.uuid4().hex[:8]
    email = f"TEST_audit_b_{suffix}@test.com"
    pw = "PassB1234!"
    r = session.post(f"{API}/auth/register", json={"email": email, "password": pw, "full_name": "Test B", "name": "Test B"})
    assert r.status_code in (200, 201), f"register B: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if not tok:
        lr = session.post(f"{API}/auth/login", json={"email": email, "password": pw})
        tok = lr.json().get("access_token") or lr.json().get("token")
    user_id = body.get("user", {}).get("id") or body.get("id")
    return {"email": email, "password": pw, "token": tok, "id": user_id}


@pytest.fixture(scope="session")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="session")
def seeded_booking(mongo, user_a):
    """Insert a confirmed booking owned by user_a directly via mongo for ownership tests."""
    # Pick any 2 terminals + a future schedule if available, otherwise synthesize ids
    term_a = mongo.terminals.find_one({}, {"_id": 0})
    term_b = mongo.terminals.find_one({"id": {"$ne": term_a["id"] if term_a else ""}}, {"_id": 0})
    sched = mongo.schedules.find_one({}, {"_id": 0}) or {
        "id": "sched_test",
        "departure_date": "2099-12-31",
        "departure_time": "10:00",
    }
    booking_id = f"TEST_AUDIT_{uuid.uuid4().hex[:10]}"
    ref_id = f"TA{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": booking_id,
        "reference": ref_id,
        "schedule_id": sched["id"],
        "from_terminal_id": (term_a or {}).get("id", "t1"),
        "to_terminal_id": (term_b or {}).get("id", "t2"),
        "departure_date": sched.get("departure_date", "2099-12-31"),
        "departure_time": sched.get("departure_time", "10:00"),
        "user_id": user_a["id"],
        "contact_email": user_a["email"],
        "contact_phone": "+60123456789",
        "passengers": [{"name": "Test A", "seat_number": "1A"}],
        "seats": ["1A"],
        "pricing": {"base": 50, "fees": 0, "discount": 0, "total": 50, "currency": "myr"},
        "status": "confirmed",
        "payment_status": "paid",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    mongo.bookings.insert_one(dict(doc))

    # Guest booking (user_id=None) for guest-email test
    guest_id = f"TEST_AUDIT_GUEST_{uuid.uuid4().hex[:10]}"
    guest_ref = f"TG{uuid.uuid4().hex[:8].upper()}"
    guest_doc = dict(doc)
    guest_doc.update({
        "id": guest_id,
        "reference": guest_ref,
        "user_id": None,
        "contact_email": "guest_buyer@example.com",
    })
    mongo.bookings.insert_one(guest_doc)

    yield {"owned_id": booking_id, "guest_id": guest_id, "guest_email": "guest_buyer@example.com"}

    mongo.bookings.delete_many({"id": {"$in": [booking_id, guest_id]}})


# ---------- C1: JWT rotation ----------
class TestC1JwtRotation:
    def test_old_jwt_with_placeholder_secret_returns_401(self, session):
        """A token signed with the OLD placeholder secret must NOT validate."""
        old_secret = "change-me-in-production-please-use-a-real-secret"
        forged = pyjwt.encode(
            {"sub": "any", "email": "x@y.z", "exp": 9999999999},
            old_secret,
            algorithm="HS256",
        )
        r = session.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401, f"Expected 401 for old/forged JWT, got {r.status_code} {r.text}"

    def test_random_garbage_jwt_returns_401(self, session):
        r = session.get(
            f"{API}/auth/me",
            headers={"Authorization": "Bearer not-a-real-jwt-token"},
        )
        assert r.status_code == 401

    def test_new_login_jwt_works(self, session, admin_token):
        r = session.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("email") == ADMIN_EMAIL


# ---------- C2: CORS Allow-Credentials must NOT be true ----------
class TestC2Cors:
    def test_health_no_allow_credentials_header(self, session):
        r = session.get(f"{API}/health", headers={"Origin": "https://evil.example.com"})
        assert r.status_code == 200
        # Header may be absent (preferred) or "false". Must NOT be "true".
        h = r.headers.get("Access-Control-Allow-Credentials", "").lower()
        assert h != "true", f"Allow-Credentials must not be true with wildcard origin, got: {h!r}"

    def test_bearer_token_works_cross_origin(self, session, admin_token):
        r = session.get(
            f"{API}/auth/me",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Origin": "https://some-other-domain.example.com",
            },
        )
        assert r.status_code == 200


# ---------- C3: Bootstrap admin login ----------
class TestC3Bootstrap:
    def test_admin_login_succeeds(self, session):
        r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("user", {}).get("is_admin") is True or body.get("user", {}).get("role") == "admin"


# ---------- C4: Booking ownership ----------
class TestC4BookingOwnership:
    def test_owner_can_get_own_booking(self, session, seeded_booking, user_a):
        r = session.get(
            f"{API}/bookings/{seeded_booking['owned_id']}",
            headers={"Authorization": f"Bearer {user_a['token']}"},
        )
        assert r.status_code == 200
        assert r.json()["id"] == seeded_booking["owned_id"]

    def test_other_user_cannot_get_someone_elses_booking(self, session, seeded_booking, user_b):
        r = session.get(
            f"{API}/bookings/{seeded_booking['owned_id']}",
            headers={"Authorization": f"Bearer {user_b['token']}"},
        )
        assert r.status_code == 403, f"Expected 403 for non-owner, got {r.status_code} {r.text}"

    def test_admin_can_get_any_booking(self, session, seeded_booking, admin_headers):
        r = session.get(
            f"{API}/bookings/{seeded_booking['owned_id']}",
            headers=admin_headers,
        )
        assert r.status_code == 200

    def test_guest_with_correct_email_gets_200(self, session, seeded_booking):
        r = session.get(
            f"{API}/bookings/{seeded_booking['guest_id']}",
            params={"email": seeded_booking["guest_email"]},
        )
        assert r.status_code == 200

    def test_guest_with_wrong_email_gets_403(self, session, seeded_booking):
        r = session.get(
            f"{API}/bookings/{seeded_booking['guest_id']}",
            params={"email": "wrong@example.com"},
        )
        assert r.status_code == 403

    def test_guest_no_email_gets_403(self, session, seeded_booking):
        r = session.get(f"{API}/bookings/{seeded_booking['guest_id']}")
        assert r.status_code == 403


# ---------- H1: Regex injection safety ----------
class TestH1RegexInjection:
    @pytest.mark.parametrize("q", [".*(.*).*", "^(a+)+$", "(((((((((a))))))))).*", "[invalid"])
    def test_terminals_regex_injection_safe(self, session, q):
        r = session.get(f"{API}/terminals", params={"q": q}, timeout=10)
        assert r.status_code == 200, f"q={q!r} => {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("q", [".*(.*).*", "^(a+)+$", "[bad", "(((.+)+)+)"])
    def test_audit_logs_regex_injection_safe(self, session, q, admin_headers):
        r = session.get(
            f"{API}/admin/audit-logs",
            params={"actor_email": q},
            headers=admin_headers,
            timeout=10,
        )
        assert r.status_code == 200, f"q={q!r} => {r.status_code} {r.text[:200]}"


# ---------- H4: payment options auth ----------
class TestH4PaymentOptionsAuth:
    def test_no_auth_no_email_403(self, session, seeded_booking):
        r = session.get(f"{API}/payments/options/{seeded_booking['guest_id']}")
        assert r.status_code == 403

    def test_admin_auth_200(self, session, seeded_booking, admin_headers):
        r = session.get(
            f"{API}/payments/options/{seeded_booking['guest_id']}",
            headers=admin_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "options" in body and "amount" in body

    def test_matching_email_200(self, session, seeded_booking):
        r = session.get(
            f"{API}/payments/options/{seeded_booking['guest_id']}",
            params={"email": seeded_booking["guest_email"]},
        )
        assert r.status_code == 200

    def test_owner_auth_200(self, session, seeded_booking, user_a):
        r = session.get(
            f"{API}/payments/options/{seeded_booking['owned_id']}",
            headers={"Authorization": f"Bearer {user_a['token']}"},
        )
        assert r.status_code == 200

    def test_other_user_403(self, session, seeded_booking, user_b):
        r = session.get(
            f"{API}/payments/options/{seeded_booking['owned_id']}",
            headers={"Authorization": f"Bearer {user_b['token']}"},
        )
        assert r.status_code == 403


# ---------- H2: Search shape + seats_available ----------
class TestSearchShape:
    def test_search_returns_schedules_with_seats_available(self, session, mongo):
        # Find any existing schedule
        sched = mongo.schedules.find_one({}, {"_id": 0})
        if not sched:
            pytest.skip("No schedules in db to test search")
        r = session.get(
            f"{API}/search",
            params={
                "from_terminal_id": sched["from_terminal_id"],
                "to_terminal_id": sched["to_terminal_id"],
                "date": sched["departure_date"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "schedules" in body
        assert isinstance(body["schedules"], list)
        for s in body["schedules"]:
            assert "seats_available" in s
            assert isinstance(s["seats_available"], int)
            assert 0 <= s["seats_available"] <= s["total_seats"]


# ---------- H5: popular_now cache ----------
class TestPopularCache:
    def test_back_to_back_returns_same_generated_at(self, session):
        r1 = session.get(f"{API}/popular/now")
        r2 = session.get(f"{API}/popular/now")
        assert r1.status_code == 200 and r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        # endpoint may return list or dict — handle both
        ts1 = b1.get("generated_at") if isinstance(b1, dict) else None
        ts2 = b2.get("generated_at") if isinstance(b2, dict) else None
        if ts1 is None:
            pytest.skip("popular/now does not expose generated_at; skipping cache equality check")
        assert ts1 == ts2, f"Cache miss within TTL window: {ts1} vs {ts2}"


# ---------- M1: my_bookings populated terminals ----------
class TestMyBookings:
    def test_my_bookings_has_from_to_objects(self, session, user_a, seeded_booking):
        r = session.get(
            f"{API}/bookings/me",
            headers={"Authorization": f"Bearer {user_a['token']}"},
        )
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        if not items:
            pytest.skip("user_a has no bookings (seed may have failed)")
        b = next((x for x in items if x.get("id") == seeded_booking["owned_id"]), items[0])
        assert "from" in b and "to" in b
        # from/to should be a dict with terminal info, or None if terminal not in db
        if b["from"] is not None:
            assert isinstance(b["from"], dict)
            assert "id" in b["from"] or "city" in b["from"] or "name" in b["from"]


# ---------- M3: admin stats + feedback summary shape ----------
class TestAdminStatsShape:
    def test_admin_stats_shape(self, session, admin_headers):
        r = session.get(f"{API}/admin/stats", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        for key in ("users", "bookings", "confirmed_bookings", "terminals", "schedules"):
            assert key in body, f"missing key {key} in admin/stats: {list(body.keys())}"
            assert isinstance(body[key], int)

    def test_admin_feedback_shape(self, session, admin_headers):
        r = session.get(f"{API}/admin/feedback", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert "summary" in body and "items" in body
        s = body["summary"]
        for key in ("total", "new", "in_progress", "resolved"):
            assert key in s, f"missing summary.{key}: {list(s.keys())}"
        assert isinstance(body["items"], list)


# ---------- Regression: cancellation-quote auth ----------
class TestCancellationQuoteRegression:
    def test_no_auth_401(self, session, seeded_booking):
        r = session.get(f"{API}/bookings/{seeded_booking['owned_id']}/cancellation-quote")
        assert r.status_code == 401

    def test_non_owner_403(self, session, seeded_booking, user_b):
        r = session.get(
            f"{API}/bookings/{seeded_booking['owned_id']}/cancellation-quote",
            headers={"Authorization": f"Bearer {user_b['token']}"},
        )
        assert r.status_code == 403
