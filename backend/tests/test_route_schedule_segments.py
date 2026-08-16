"""End-to-end tests for the Routes ↔ Schedules ↔ Bookings model.

Verifies:
  1. A route with 4 stops + full pairing matrix can be created.
  2. Creating a schedule linked to a route forces from = route.origin, to = destination.
  3. Attempting a duplicate (same route+date+time) is rejected.
  4. Search for a mid-route segment (Melaka → JB) surfaces the same physical bus.
  5. Booking Melaka → JB on a bus blocks that seat for any other segment search
     on the SAME schedule (KL → SG, Melaka → SG, etc.) — Option A full-trip block.
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


def _make_route_with_stops(admin_headers, kl_sentral, melaka, jb, gmc):
    """Create a KL Sentral → Melaka → JB → GMC route with full pairing matrix."""
    code = f"E2E{uuid.uuid4().hex[:6].upper()}"
    body = {
        "code": code,
        "name": "E2E KL-Melaka-JB-SG",
        "origin_city": kl_sentral["city"],
        "destination_city": gmc["city"],
        "boarding_stops": [
            {"terminal_id": kl_sentral["id"], "offset_min": 0},
            {"terminal_id": melaka["id"], "offset_min": 120},
            {"terminal_id": jb["id"], "offset_min": 360},
        ],
        "alighting_stops": [
            {"terminal_id": melaka["id"], "offset_min": 120},
            {"terminal_id": jb["id"], "offset_min": 360},
            {"terminal_id": gmc["id"], "offset_min": 420},
        ],
        # All valid (pickup, dropoff) combos going forward through the route.
        "pairings": [
            {"pickup_id": kl_sentral["id"], "dropoff_id": melaka["id"],
             "adult_fare": 30, "child_fare": 15, "currency": "myr"},
            {"pickup_id": kl_sentral["id"], "dropoff_id": jb["id"],
             "adult_fare": 55, "child_fare": 27, "currency": "myr"},
            {"pickup_id": kl_sentral["id"], "dropoff_id": gmc["id"],
             "adult_fare": 70, "child_fare": 35, "currency": "myr"},
            {"pickup_id": melaka["id"], "dropoff_id": jb["id"],
             "adult_fare": 28, "child_fare": 14, "currency": "myr"},
            {"pickup_id": melaka["id"], "dropoff_id": gmc["id"],
             "adult_fare": 40, "child_fare": 20, "currency": "myr"},
            {"pickup_id": jb["id"], "dropoff_id": gmc["id"],
             "adult_fare": 15, "child_fare": 7, "currency": "myr"},
        ],
    }
    r = requests.post(f"{BASE}/api/admin/routes", json=body, headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def test_route_schedule_segment_flow():
    tok = admin_token()
    h = {"Authorization": f"Bearer {tok}"}

    # Fetch terminals used for the fixture route.
    terms = requests.get(f"{BASE}/api/admin/terminals", headers=h, timeout=10).json()
    kl = _find_terminal_by_city(terms, "Kuala Lumpur")
    melaka = _find_terminal_by_city(terms, "Melaka")
    jb = _find_terminal_by_city(terms, "Johor Bahru")
    gmc = _find_terminal_by_city(terms, "Singapore")

    route = _make_route_with_stops(h, kl, melaka, jb, gmc)
    route_id = route["id"]

    try:
        # 1. Create a schedule linked to the route. from/to should be forced to
        #    route origin (KL) and destination (GMC) even if we submit different values.
        dep_date = f"2099-01-{(int(time.time()) % 27) + 1:02d}"
        dep_time = f"{(int(time.time()) % 20) + 3:02d}:15"
        sched_body = {
            "from_terminal_id": jb["id"],       # intentionally wrong → should be overridden
            "to_terminal_id": melaka["id"],     # intentionally wrong → should be overridden
            "departure_date": dep_date,
            "departure_time": dep_time,
            "arrival_time": "20:00",
            "bus_type": "Standard",
            "adult_fare": 55,
            "child_fare": 27,
            "total_seats": 30,
            "route_id": route_id,
        }
        r = requests.post(f"{BASE}/api/admin/schedules", json=sched_body, headers=h, timeout=10)
        assert r.status_code == 200, r.text
        sched = r.json()
        assert sched["from_terminal_id"] == kl["id"], "Route-linked schedule must force from = route origin"
        assert sched["to_terminal_id"] == gmc["id"], "Route-linked schedule must force to = route destination"
        sched_id = sched["id"]

        # 2. Duplicate (same route+date+time) must be rejected.
        r = requests.post(f"{BASE}/api/admin/schedules", json=sched_body, headers=h, timeout=10)
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"].lower()

        # 3. Search for mid-segment Melaka → JB — same physical bus should surface
        #    with the segment fare (RM 28) baked in.
        r = requests.get(
            f"{BASE}/api/search",
            params={"date": dep_date, "from_terminal_id": melaka["id"], "to_terminal_id": jb["id"]},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        found = [s for s in r.json()["schedules"] if s["id"] == sched_id]
        assert len(found) == 1, "Mid-segment search must surface the physical bus"
        assert found[0]["adult_fare"] == 28, "Segment fare must reflect route.pairing (RM 28)"
        assert found[0].get("is_route_segment") is True

        # 4. Same search but as full trip (KL → SG) → the schedule shows up with its own from/to fare.
        r = requests.get(
            f"{BASE}/api/search",
            params={"date": dep_date, "from_terminal_id": kl["id"], "to_terminal_id": gmc["id"]},
            timeout=10,
        )
        assert r.status_code == 200
        found_full = [s for s in r.json()["schedules"] if s["id"] == sched_id]
        assert len(found_full) == 1
        assert found_full[0]["adult_fare"] == 55, "Full-trip fare should be schedule's own fare (RM 55)"

        # 5. Lock seat 5 on this schedule, create a booking for Melaka → JB.
        r = requests.post(
            f"{BASE}/api/seats/lock",
            json={"schedule_id": sched_id, "seat_numbers": ["5A"]},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        # Booking body — sold as Melaka → JB segment.
        booking_body = {
            "schedule_id": sched_id,
            "seat_assignments": [{"seat_number": "5A", "passenger_index": 0}],
            "passengers": [{"name": "E2E Test", "category": "adult"}],
            "contact_email": "e2e@test.com",
            "contact_phone": "+60123456789",
            "pickup_terminal_id": melaka["id"],
            "dropoff_terminal_id": jb["id"],
        }
        r = requests.post(f"{BASE}/api/bookings", json=booking_body, timeout=10)
        assert r.status_code == 200, r.text
        booking = r.json()
        assert booking["from_terminal_id"] == melaka["id"]
        assert booking["to_terminal_id"] == jb["id"]
        # Fare on the booking must match the pairing (RM 28), NOT the schedule's own RM 55.
        assert booking["pricing"]["adult_fare"] == 28
        assert booking["pricing"]["total"] == 28

        # 6. Full-trip block: seat 5A is now unavailable to another Melaka→SG search
        #    on the same schedule (Option A behaviour).
        r = requests.get(
            f"{BASE}/api/search",
            params={"date": dep_date, "from_terminal_id": melaka["id"], "to_terminal_id": gmc["id"]},
            timeout=10,
        )
        assert r.status_code == 200
        found_ms = [s for s in r.json()["schedules"] if s["id"] == sched_id]
        assert len(found_ms) == 1
        # Total seats = 30, we've locked 1 → 29 available across ALL segment searches.
        assert found_ms[0]["seats_available"] == 29

    finally:
        # Cleanup: bulk-delete schedules for this test route (force=True to bypass
        # the booking guard since our test created a real booking), then the route.
        try:
            requests.post(
                f"{BASE}/api/admin/schedules/bulk-delete",
                json={"route_id": route_id, "force": True},
                headers=h,
                timeout=15,
            )
            r = requests.delete(f"{BASE}/api/admin/routes/{route_id}", headers=h, timeout=10)
            # Some route deletes fail if any schedule slipped through — log for CI visibility.
            if r.status_code >= 400:
                print(f"[cleanup] route delete returned {r.status_code}: {r.text[:200]}")
        except Exception as ce:
            print(f"[cleanup] error: {ce}")
