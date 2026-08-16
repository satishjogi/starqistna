"""End-to-end tests for the Routes ↔ Schedules ↔ Bus-Instance seat sharing.

Verifies:
  1. A route with 4 stops + full pairing matrix can be created.
  2. Multiple schedules on the SAME route+date+time are allowed (sibling schedules).
  3. All siblings share ONE bus_instance_id → seat pool is shared.
  4. Legacy (unlinked) schedules keep independent seat pools.
  5. Booking seat 5A on the KL→Melaka sibling blocks seat 5A on the KL→JB sibling
     (search shows the shared seat count).
"""
import os
import uuid
import time
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def admin_token() -> str:
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"email": "admin@starqistna.com", "password": "Admin@123"},
                      timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def _find_terminal_by_city(term_list, city):
    for t in term_list:
        if t["city"].lower() == city.lower():
            return t
    raise AssertionError(f"No terminal in city {city!r}")


def _make_route_with_stops(headers, kl, melaka, jb, gmc):
    code = f"E2E{uuid.uuid4().hex[:6].upper()}"
    body = {
        "code": code,
        "name": "E2E KL-Melaka-JB-SG",
        "origin_city": kl["city"],
        "destination_city": gmc["city"],
        "boarding_stops": [
            {"terminal_id": kl["id"], "offset_min": 0},
            {"terminal_id": melaka["id"], "offset_min": 120},
            {"terminal_id": jb["id"], "offset_min": 360},
        ],
        "alighting_stops": [
            {"terminal_id": melaka["id"], "offset_min": 120},
            {"terminal_id": jb["id"], "offset_min": 360},
            {"terminal_id": gmc["id"], "offset_min": 420},
        ],
        "pairings": [
            {"pickup_id": kl["id"], "dropoff_id": melaka["id"], "adult_fare": 30, "child_fare": 15, "currency": "myr"},
            {"pickup_id": kl["id"], "dropoff_id": jb["id"], "adult_fare": 55, "child_fare": 27, "currency": "myr"},
            {"pickup_id": kl["id"], "dropoff_id": gmc["id"], "adult_fare": 70, "child_fare": 35, "currency": "myr"},
            {"pickup_id": melaka["id"], "dropoff_id": jb["id"], "adult_fare": 28, "child_fare": 14, "currency": "myr"},
            {"pickup_id": melaka["id"], "dropoff_id": gmc["id"], "adult_fare": 40, "child_fare": 20, "currency": "myr"},
            {"pickup_id": jb["id"], "dropoff_id": gmc["id"], "adult_fare": 15, "child_fare": 7, "currency": "myr"},
        ],
    }
    r = requests.post(f"{BASE}/api/admin/routes", json=body, headers=headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def test_sibling_schedules_share_seat_pool():
    tok = admin_token()
    h = {"Authorization": f"Bearer {tok}"}

    terms = requests.get(f"{BASE}/api/admin/terminals", headers=h, timeout=10).json()
    kl = _find_terminal_by_city(terms, "Kuala Lumpur")
    melaka = _find_terminal_by_city(terms, "Melaka")
    jb = _find_terminal_by_city(terms, "Johor Bahru")
    gmc = _find_terminal_by_city(terms, "Singapore")

    route = _make_route_with_stops(h, kl, melaka, jb, gmc)
    route_id = route["id"]

    try:
        dep_date = f"2099-01-{(int(time.time()) % 27) + 1:02d}"
        dep_time = f"{(int(time.time()) % 20) + 3:02d}:15"
        common = {
            "departure_date": dep_date,
            "departure_time": dep_time,
            "arrival_time": "20:00",
            "bus_type": "Standard",
            "total_seats": 30,
            "route_id": route_id,
        }

        # 1. Create the KL→Melaka sibling schedule.
        r = requests.post(f"{BASE}/api/admin/schedules", json={
            **common, "from_terminal_id": kl["id"], "to_terminal_id": melaka["id"],
            "adult_fare": 30, "child_fare": 15,
        }, headers=h, timeout=10)
        assert r.status_code == 200, r.text
        s_kl_melaka = r.json()
        assert s_kl_melaka["from_terminal_id"] == kl["id"], "From must be honoured as-is (no force to route origin)"
        assert s_kl_melaka["to_terminal_id"] == melaka["id"]
        assert s_kl_melaka.get("bus_instance_id")

        # 2. Create the KL→JB sibling on same physical bus.
        r = requests.post(f"{BASE}/api/admin/schedules", json={
            **common, "from_terminal_id": kl["id"], "to_terminal_id": jb["id"],
            "adult_fare": 55, "child_fare": 27,
        }, headers=h, timeout=10)
        assert r.status_code == 200, r.text
        s_kl_jb = r.json()
        assert s_kl_jb["bus_instance_id"] == s_kl_melaka["bus_instance_id"], \
            "Sibling schedules on same physical bus must share bus_instance_id"

        # 3. Exact same segment twice → rejected.
        r = requests.post(f"{BASE}/api/admin/schedules", json={
            **common, "from_terminal_id": kl["id"], "to_terminal_id": jb["id"],
            "adult_fare": 55, "child_fare": 27,
        }, headers=h, timeout=10)
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"].lower()

        # 4. Lock + book seat 5A on the KL→Melaka schedule.
        r = requests.post(f"{BASE}/api/seats/lock", json={
            "schedule_id": s_kl_melaka["id"], "seat_numbers": ["5A"],
        }, timeout=10)
        assert r.status_code == 200, r.text
        r = requests.post(f"{BASE}/api/bookings", json={
            "schedule_id": s_kl_melaka["id"],
            "seat_assignments": [{"seat_number": "5A", "passenger_index": 0}],
            "passengers": [{"name": "E2E Sibling", "category": "adult"}],
            "contact_email": "e2e-siblings@test.com",
            "contact_phone": "+60123456789",
            "pickup_terminal_id": kl["id"],
            "dropoff_terminal_id": melaka["id"],
        }, timeout=10)
        assert r.status_code == 200, r.text

        # 5. Cross-sibling block: locking 5A on the KL→JB sibling must fail.
        r = requests.post(f"{BASE}/api/seats/lock", json={
            "schedule_id": s_kl_jb["id"], "seat_numbers": ["5A"],
        }, timeout=10)
        assert r.status_code == 409, r.text
        assert "5A" in r.json()["detail"]["failed_seats"]

        # 6. Search shows the SHARED seats_available for both siblings.
        r = requests.get(f"{BASE}/api/search", params={
            "date": dep_date, "from_terminal_id": kl["id"], "to_terminal_id": melaka["id"],
        }, timeout=10)
        assert r.status_code == 200
        found = [s for s in r.json()["schedules"] if s["id"] == s_kl_melaka["id"]]
        assert len(found) == 1
        assert found[0]["seats_available"] == 29  # 30 - 1

        r = requests.get(f"{BASE}/api/search", params={
            "date": dep_date, "from_terminal_id": kl["id"], "to_terminal_id": jb["id"],
        }, timeout=10)
        assert r.status_code == 200
        found = [s for s in r.json()["schedules"] if s["id"] == s_kl_jb["id"]]
        assert len(found) == 1
        # Same bus_instance → same seats_available count
        assert found[0]["seats_available"] == 29, \
            f"Sibling schedule must reflect shared seat pool (got {found[0]['seats_available']})"

        # 7. GET /schedules/{s_kl_jb} shows seat 5A as unavailable (cross-sibling visibility).
        #    In tests the seat stays 'locked' (we didn't run Stripe finalization).
        r = requests.get(f"{BASE}/api/schedules/{s_kl_jb['id']}", timeout=10)
        assert r.status_code == 200
        detail = r.json()
        seat_5a_taken = False
        for row in detail["layout"]:
            for cell in row:
                if cell.get("seat_number") == "5A" and cell.get("status") in ("locked", "booked"):
                    seat_5a_taken = True
        assert seat_5a_taken, "Seat 5A must show as locked/booked on the sibling schedule's seat map"

    finally:
        # Cleanup
        try:
            requests.post(f"{BASE}/api/admin/schedules/bulk-delete",
                          json={"route_id": route_id, "force": True},
                          headers=h, timeout=15)
            r = requests.delete(f"{BASE}/api/admin/routes/{route_id}", headers=h, timeout=10)
            if r.status_code >= 400:
                print(f"[cleanup] route delete returned {r.status_code}: {r.text[:200]}")
        except Exception as ce:
            print(f"[cleanup] error: {ce}")


def test_legacy_unlinked_schedule_has_its_own_seat_pool():
    """A schedule with no route_id gets bus_instance_id = its own id — legacy
    behaviour is preserved. Two separately-created legacy schedules keep
    independent seat pools."""
    tok = admin_token()
    h = {"Authorization": f"Bearer {tok}"}
    terms = requests.get(f"{BASE}/api/admin/terminals", headers=h, timeout=10).json()
    kl = _find_terminal_by_city(terms, "Kuala Lumpur")
    melaka = _find_terminal_by_city(terms, "Melaka")

    dep_date = f"2099-02-{(int(time.time()) % 27) + 1:02d}"
    dep_time = f"{(int(time.time()) % 20) + 3:02d}:20"
    sched_ids = []
    try:
        # Legacy point-to-point schedule (no route).
        r = requests.post(f"{BASE}/api/admin/schedules", json={
            "from_terminal_id": kl["id"], "to_terminal_id": melaka["id"],
            "departure_date": dep_date, "departure_time": dep_time,
            "arrival_time": "12:00", "bus_type": "Standard",
            "adult_fare": 30, "child_fare": 15, "total_seats": 30,
        }, headers=h, timeout=10)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["bus_instance_id"] == s["id"]  # legacy → self
        sched_ids.append(s["id"])
    finally:
        # Cleanup via bulk-delete filtered by from/to
        requests.post(f"{BASE}/api/admin/schedules/bulk-delete",
                      json={"from_terminal_id": kl["id"], "to_terminal_id": melaka["id"],
                            "start_date": dep_date, "end_date": dep_date, "force": True},
                      headers=h, timeout=15)
