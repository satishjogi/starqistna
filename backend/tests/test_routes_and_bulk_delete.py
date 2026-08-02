"""P0 tests: Admin Routes CRUD, schedule PATCH, bulk-delete preview/execute,
terminal optional fields, auth regression."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@starqistna.com"
ADMIN_PASSWORD = "Admin@123"


# ---------- fixtures ----------
@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert not data.get("must_change_password"), "admin flagged must_change_password — blocker"
    return data["access_token"]


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def terminals(auth_headers):
    r = requests.get(f"{API}/admin/terminals", headers=auth_headers, timeout=15)
    assert r.status_code == 200
    return r.json()


def _find(terms, code):
    for t in terms:
        if t["code"] == code:
            return t
    return None


# ---------- Auth regression ----------
class TestAuth:
    def test_admin_login_success(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["user"]["is_admin"] is True

    def test_google_callback_fake_code_rejected(self):
        r = requests.post(f"{API}/auth/google/callback", json={"code": "fake-nonsense-code", "redirect_uri": "http://x/y"})
        assert r.status_code in (400, 401), f"unexpected {r.status_code} {r.text[:200]}"


# ---------- Routes CRUD ----------
class TestRoutesCRUD:
    created_route_id = None
    code = f"TEST-{uuid.uuid4().hex[:6].upper()}"

    def test_create_route(self, auth_headers, terminals):
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")
        assert tbs and gmc
        body = {
            "code": self.__class__.code,
            "name": "Test route KL→SG",
            "origin_city": "Kuala Lumpur",
            "destination_city": "Singapore",
            "direction": "KL-SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [{"pickup_id": tbs["id"], "dropoff_id": gmc["id"], "adult_fare": 80, "child_fare": 50, "senior_fare": 70, "oku_fare": 60}],
            "is_active": True,
        }
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["code"] == self.__class__.code
        assert "id" in data
        assert "_id" not in data
        TestRoutesCRUD.created_route_id = data["id"]

    def test_get_route(self, auth_headers):
        assert TestRoutesCRUD.created_route_id
        r = requests.get(f"{API}/admin/routes/{TestRoutesCRUD.created_route_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["code"] == self.__class__.code

    def test_duplicate_code_rejected(self, auth_headers, terminals):
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")
        body = {
            "code": self.__class__.code,
            "name": "dup",
            "origin_city": "KL",
            "destination_city": "SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [],
        }
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json=body)
        assert r.status_code == 400
        assert "exists" in r.text.lower()

    def test_route_invalid_pairing_rejected(self, auth_headers, terminals):
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")
        klsentral = _find(terminals, "KLS")  # not in alighting_stops
        body = {
            "code": f"TEST-BAD-{uuid.uuid4().hex[:4].upper()}",
            "name": "bad pair",
            "origin_city": "KL",
            "destination_city": "SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [{"pickup_id": tbs["id"], "dropoff_id": klsentral["id"]}],
        }
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json=body)
        assert r.status_code == 400
        assert "dropoff" in r.text.lower() or "alighting" in r.text.lower()

    def test_patch_route(self, auth_headers):
        r = requests.patch(f"{API}/admin/routes/{TestRoutesCRUD.created_route_id}", headers=auth_headers,
                           json={"name": "Test route updated"})
        assert r.status_code == 200
        assert r.json()["name"] == "Test route updated"

    def test_delete_route(self, auth_headers):
        r = requests.delete(f"{API}/admin/routes/{TestRoutesCRUD.created_route_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        # verify gone
        r2 = requests.get(f"{API}/admin/routes/{TestRoutesCRUD.created_route_id}", headers=auth_headers)
        assert r2.status_code == 404


# ---------- Schedule route-linking validation ----------
class TestScheduleRouteLink:
    def test_create_schedule_from_terminal_not_in_route_boarding(self, auth_headers, terminals):
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")
        klsentral = _find(terminals, "KLS")

        # Create a route with only TBS boarding
        rcode = f"TEST-LINK-{uuid.uuid4().hex[:4].upper()}"
        route_body = {
            "code": rcode, "name": "link check", "origin_city": "KL", "destination_city": "SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [],
        }
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json=route_body)
        assert r.status_code == 200, r.text
        route_id = r.json()["id"]

        # Try to create schedule with from=KLS which is NOT in boarding_stops
        sched = {
            "from_terminal_id": klsentral["id"],
            "to_terminal_id": gmc["id"],
            "departure_date": "2026-06-01",
            "departure_time": "22:00",
            "arrival_time": "05:00",
            "adult_fare": 80,
            "child_fare": 50,
            "total_seats": 40,
            "route_id": route_id,
        }
        r2 = requests.post(f"{API}/admin/schedules", headers=auth_headers, json=sched)
        assert r2.status_code == 400
        assert "boarding" in r2.text.lower()

        # cleanup
        requests.delete(f"{API}/admin/routes/{route_id}", headers=auth_headers)


# ---------- PATCH /admin/schedules/{id} ----------
class TestSchedulePatch:
    def test_patch_schedule_fare_and_route(self, auth_headers, terminals):
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")

        # Create a route
        rcode = f"TEST-PATCH-{uuid.uuid4().hex[:4].upper()}"
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json={
            "code": rcode, "name": "patch route", "origin_city": "KL", "destination_city": "SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [],
        })
        assert r.status_code == 200, r.text
        route_id = r.json()["id"]

        # Create a schedule
        sched = {
            "from_terminal_id": tbs["id"], "to_terminal_id": gmc["id"],
            "departure_date": "2026-06-10", "departure_time": "22:00", "arrival_time": "05:00",
            "adult_fare": 80, "child_fare": 50, "total_seats": 40,
        }
        r = requests.post(f"{API}/admin/schedules", headers=auth_headers, json=sched)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        # PATCH fare + route_id
        r = requests.patch(f"{API}/admin/schedules/{sid}", headers=auth_headers,
                           json={"adult_fare": 99.5, "route_id": route_id})
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["adult_fare"] == 99.5
        assert doc["route_id"] == route_id

        # cleanup: delete schedule via bulk-delete, then route
        requests.post(f"{API}/admin/schedules/bulk-delete", headers=auth_headers,
                      json={"route_id": route_id, "force": True})
        requests.delete(f"{API}/admin/routes/{route_id}", headers=auth_headers)


# ---------- Bulk delete preview/execute ----------
class TestBulkDelete:
    def test_preview_empty_body_rejected(self, auth_headers):
        r = requests.post(f"{API}/admin/schedules/bulk-delete/preview", headers=auth_headers, json={})
        assert r.status_code == 400
        assert "safety guard" in r.text.lower() or "at least one filter" in r.text.lower()

    def test_preview_unlinked_only(self, auth_headers):
        r = requests.post(f"{API}/admin/schedules/bulk-delete/preview", headers=auth_headers,
                          json={"unlinked_only": True})
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data["matched"], int)
        assert isinstance(data["blocked"], int)
        assert isinstance(data["deletable"], int)
        assert data["deletable"] == data["matched"] - data["blocked"]

    def test_bulk_delete_no_match(self, auth_headers):
        # filter matching zero schedules — use a fake future date range
        r = requests.post(f"{API}/admin/schedules/bulk-delete", headers=auth_headers,
                          json={"start_date": "2099-01-01", "end_date": "2099-01-02"})
        assert r.status_code == 200
        data = r.json()
        assert data["deleted"] == 0
        assert data["skipped"] == 0

    def test_bulk_delete_skips_schedules_with_bookings(self, auth_headers, terminals):
        """Create 2 schedules, add a booking to one, bulk-delete by route — the
        one with a booking must survive when force=false."""
        tbs = _find(terminals, "TBS")
        gmc = _find(terminals, "GMC")

        # route
        rcode = f"TEST-BULK-{uuid.uuid4().hex[:4].upper()}"
        r = requests.post(f"{API}/admin/routes", headers=auth_headers, json={
            "code": rcode, "name": "bulk safe", "origin_city": "KL", "destination_city": "SG",
            "boarding_stops": [{"terminal_id": tbs["id"], "offset_min": 0}],
            "alighting_stops": [{"terminal_id": gmc["id"], "offset_min": 300}],
            "pairings": [],
        })
        assert r.status_code == 200, r.text
        route_id = r.json()["id"]

        # 2 schedules
        sids = []
        for date in ("2026-07-01", "2026-07-02"):
            rs = requests.post(f"{API}/admin/schedules", headers=auth_headers, json={
                "from_terminal_id": tbs["id"], "to_terminal_id": gmc["id"],
                "departure_date": date, "departure_time": "22:00", "arrival_time": "05:00",
                "adult_fare": 80, "child_fare": 50, "total_seats": 40,
                "route_id": route_id,
            })
            assert rs.status_code == 200, rs.text
            sids.append(rs.json()["id"])

        # Insert a fake booking directly via mongo to attach to sids[0]
        # (bypasses payment). Use pymongo sync.
        from pymongo import MongoClient
        mongo_url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
        db_name = os.environ.get("DB_NAME") or "test_database"
        client = MongoClient(mongo_url)
        booking_id = f"TEST_BULK_{uuid.uuid4().hex[:8]}"
        client[db_name].bookings.insert_one({
            "id": booking_id, "schedule_id": sids[0],
            "status": "confirmed", "user_id": None,
            "contact_email": "test_bulk@example.com",
            "booking_ref": f"TB{uuid.uuid4().hex[:6].upper()}",
        })
        try:
            # Preview
            pr = requests.post(f"{API}/admin/schedules/bulk-delete/preview", headers=auth_headers,
                               json={"route_id": route_id})
            assert pr.status_code == 200, pr.text
            pdata = pr.json()
            assert pdata["matched"] == 2
            assert pdata["blocked"] == 1
            assert pdata["deletable"] == 1

            # Execute non-force
            ex = requests.post(f"{API}/admin/schedules/bulk-delete", headers=auth_headers,
                               json={"route_id": route_id})
            assert ex.status_code == 200, ex.text
            exd = ex.json()
            assert exd["deleted"] == 1
            assert exd["skipped"] == 1

            # sids[0] (with booking) must still exist
            check = requests.post(f"{API}/admin/schedules/bulk-delete/preview", headers=auth_headers,
                                  json={"route_id": route_id})
            assert check.status_code == 200
            assert check.json()["matched"] == 1
        finally:
            # cleanup: remove booking, force-delete remaining schedules, delete route
            client[db_name].bookings.delete_one({"id": booking_id})
            requests.post(f"{API}/admin/schedules/bulk-delete", headers=auth_headers,
                          json={"route_id": route_id, "force": True})
            requests.delete(f"{API}/admin/routes/{route_id}", headers=auth_headers)
            client.close()


# ---------- Terminals optional fields ----------
class TestTerminalOptionalFields:
    def test_create_terminal_with_new_fields(self, auth_headers):
        code = f"T{uuid.uuid4().hex[:4].upper()}"
        body = {
            "city": "Testville", "name": "Test Bus Stop", "code": code,
            "country": "MY", "state": "TS",
            "landmark_address": "Near Test Park",
            "lat": 3.14, "lng": 101.7,
            "cts_code": "CTS-TEST-01",
            "is_pickup": True, "is_dropoff": False,
        }
        r = requests.post(f"{API}/admin/terminals", headers=auth_headers, json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["landmark_address"] == "Near Test Park"
        assert data["lat"] == 3.14 and data["lng"] == 101.7
        assert data["cts_code"] == "CTS-TEST-01"
        assert data["is_pickup"] is True and data["is_dropoff"] is False
        tid = data["id"]

        # GET list must include these fields intact
        gr = requests.get(f"{API}/admin/terminals", headers=auth_headers)
        assert gr.status_code == 200
        match = [t for t in gr.json() if t["id"] == tid]
        assert match, "created terminal not returned in list"
        m = match[0]
        assert m["landmark_address"] == "Near Test Park"
        assert m["cts_code"] == "CTS-TEST-01"
        assert m["is_pickup"] is True
        assert m["is_dropoff"] is False

        # cleanup
        requests.delete(f"{API}/admin/terminals/{tid}", headers=auth_headers)
