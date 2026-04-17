"""
Transit E1 - Bus Booking System Backend API Tests
Tests: Auth, Terminals, Search, Schedules, Seat Locks (double-booking protection), Bookings, Payments, Admin
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
API_URL = f"{BASE_URL}/api"

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@transit.my"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def admin_token(api_client):
    """Get admin authentication token"""
    response = api_client.post(f"{API_URL}/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"Admin authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def admin_client(api_client, admin_token):
    """Session with admin auth header"""
    api_client.headers.update({"Authorization": f"Bearer {admin_token}"})
    return api_client


@pytest.fixture(scope="module")
def test_user_data():
    """Generate unique test user data"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return {
        "email": f"TEST_user_{timestamp}@example.com",
        "password": "TestPass123",
        "full_name": f"TEST User {timestamp}",
        "phone": "+60123456789"
    }


class TestHealthAndRoot:
    """Health check and root endpoint tests"""
    
    def test_root_endpoint(self, api_client):
        """Test root API endpoint returns service info"""
        response = api_client.get(f"{API_URL}/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["status"] == "ok"
        print(f"Root endpoint OK: {data}")
    
    def test_health_endpoint(self, api_client):
        """Test health check endpoint"""
        response = api_client.get(f"{API_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "time" in data
        print(f"Health check OK: {data}")


class TestTerminals:
    """Terminal listing and search tests"""
    
    def test_list_terminals_returns_grouped_and_all(self, api_client):
        """GET /api/terminals returns grouped Malaysian terminals and all list"""
        response = api_client.get(f"{API_URL}/terminals")
        assert response.status_code == 200
        data = response.json()
        
        # Verify structure
        assert "grouped" in data
        assert "all" in data
        assert isinstance(data["grouped"], list)
        assert isinstance(data["all"], list)
        
        # Verify we have terminals
        assert len(data["all"]) > 0, "Should have seeded terminals"
        
        # Verify grouped structure
        for group in data["grouped"]:
            assert "city" in group
            assert "terminals" in group
            assert isinstance(group["terminals"], list)
        
        # Verify terminal structure
        terminal = data["all"][0]
        assert "id" in terminal
        assert "city" in terminal
        assert "name" in terminal
        assert "code" in terminal
        
        print(f"Terminals: {len(data['all'])} total, {len(data['grouped'])} cities")
    
    def test_terminals_search_filter(self, api_client):
        """Test terminal search with query parameter"""
        response = api_client.get(f"{API_URL}/terminals", params={"q": "KL"})
        assert response.status_code == 200
        data = response.json()
        
        # Should filter to KL-related terminals
        assert len(data["all"]) > 0
        for t in data["all"]:
            assert "KL" in t["city"].upper() or "KL" in t["name"].upper() or "KL" in t["code"].upper()
        print(f"KL search returned {len(data['all'])} terminals")


class TestAuth:
    """Authentication endpoint tests"""
    
    def test_admin_login_success(self, api_client):
        """POST /api/auth/login with admin credentials returns JWT"""
        response = api_client.post(f"{API_URL}/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        data = response.json()
        
        assert "access_token" in data
        assert "user" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == ADMIN_EMAIL.lower()
        assert data["user"]["is_admin"] == True
        print(f"Admin login successful: {data['user']['email']}")
    
    def test_login_invalid_credentials(self, api_client):
        """Test login with wrong password returns 401"""
        response = api_client.post(f"{API_URL}/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "WrongPassword123"
        })
        assert response.status_code == 401
        print("Invalid credentials correctly rejected")
    
    def test_register_new_user(self, api_client, test_user_data):
        """POST /api/auth/register creates a user and returns JWT"""
        response = api_client.post(f"{API_URL}/auth/register", json=test_user_data)
        assert response.status_code == 200
        data = response.json()
        
        assert "access_token" in data
        assert "user" in data
        assert data["user"]["email"] == test_user_data["email"].lower()
        assert data["user"]["full_name"] == test_user_data["full_name"]
        assert data["user"]["is_admin"] == False
        print(f"User registered: {data['user']['email']}")
        
        # Store token for later tests
        test_user_data["token"] = data["access_token"]
        test_user_data["user_id"] = data["user"]["id"]
    
    def test_auth_me_with_token(self, api_client, test_user_data):
        """GET /api/auth/me with Bearer token returns user"""
        if "token" not in test_user_data:
            pytest.skip("No user token available")
        
        headers = {"Authorization": f"Bearer {test_user_data['token']}"}
        response = api_client.get(f"{API_URL}/auth/me", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        assert data["email"] == test_user_data["email"].lower()
        print(f"Auth/me returned: {data['email']}")
    
    def test_auth_me_without_token(self, api_client):
        """GET /api/auth/me without token returns 401"""
        # Create fresh session without auth
        fresh_session = requests.Session()
        response = fresh_session.get(f"{API_URL}/auth/me")
        assert response.status_code == 401
        print("Auth/me correctly requires authentication")
    
    def test_register_duplicate_email(self, api_client, test_user_data):
        """Test registering with existing email returns 400"""
        response = api_client.post(f"{API_URL}/auth/register", json=test_user_data)
        assert response.status_code == 400
        assert "already registered" in response.json().get("detail", "").lower()
        print("Duplicate email correctly rejected")


class TestSearchAndSchedules:
    """Search and schedule endpoint tests"""
    
    @pytest.fixture(scope="class")
    def terminal_ids(self, api_client):
        """Get terminal IDs for KL Sentral and Sungai Nibong"""
        response = api_client.get(f"{API_URL}/terminals")
        terminals = response.json()["all"]
        
        kl_sentral = next((t for t in terminals if "KL Sentral" in t["name"]), None)
        sungai_nibong = next((t for t in terminals if "Sungai Nibong" in t["name"]), None)
        
        if not kl_sentral or not sungai_nibong:
            pytest.skip("Required terminals not found")
        
        return {"from": kl_sentral["id"], "to": sungai_nibong["id"]}
    
    def test_search_schedules(self, api_client, terminal_ids):
        """GET /api/search with from/to/date returns schedules"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        response = api_client.get(f"{API_URL}/search", params={
            "from_terminal_id": terminal_ids["from"],
            "to_terminal_id": terminal_ids["to"],
            "date": tomorrow
        })
        assert response.status_code == 200
        data = response.json()
        
        assert "from" in data
        assert "to" in data
        assert "date" in data
        assert "schedules" in data
        assert data["date"] == tomorrow
        
        # Verify schedule structure if any exist
        if len(data["schedules"]) > 0:
            sched = data["schedules"][0]
            assert "id" in sched
            assert "departure_time" in sched
            assert "arrival_time" in sched
            assert "adult_fare" in sched
            assert "seats_available" in sched
            assert "total_seats" in sched
            print(f"Found {len(data['schedules'])} schedules for {tomorrow}")
        else:
            print(f"No schedules found for {tomorrow} (may need different date)")
    
    def test_get_schedule_with_seat_layout(self, api_client, terminal_ids):
        """GET /api/schedules/{id} returns seat layout (rows with A/B/aisle/C/D)"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # First get a schedule
        search_resp = api_client.get(f"{API_URL}/search", params={
            "from_terminal_id": terminal_ids["from"],
            "to_terminal_id": terminal_ids["to"],
            "date": tomorrow
        })
        schedules = search_resp.json().get("schedules", [])
        
        if not schedules:
            pytest.skip("No schedules available for testing")
        
        schedule_id = schedules[0]["id"]
        
        # Get schedule details with layout
        response = api_client.get(f"{API_URL}/schedules/{schedule_id}")
        assert response.status_code == 200
        data = response.json()
        
        assert "schedule" in data
        assert "from" in data
        assert "to" in data
        assert "layout" in data
        
        # Verify layout structure (rows with A/B/aisle/C/D)
        layout = data["layout"]
        assert isinstance(layout, list)
        assert len(layout) > 0
        
        # Check first row structure
        row = layout[0]
        assert len(row) == 5  # A, B, aisle, C, D
        
        # Verify seat structure
        seat_cols = [s for s in row if not s.get("aisle")]
        aisle = [s for s in row if s.get("aisle")]
        assert len(seat_cols) == 4  # A, B, C, D
        assert len(aisle) == 1  # One aisle
        
        for seat in seat_cols:
            assert "seat_number" in seat
            assert "status" in seat
            assert seat["status"] in ["available", "locked", "booked"]
        
        print(f"Schedule {schedule_id} has {len(layout)} rows, layout verified")
        return schedule_id


class TestSeatLockingDoubleBookProtection:
    """Seat locking and double-booking protection tests - PRIMARY FUNCTIONAL REQUIREMENT"""
    
    @pytest.fixture(scope="class")
    def available_schedule(self, api_client):
        """Get a schedule with available seats"""
        # Get terminals
        terminals_resp = api_client.get(f"{API_URL}/terminals")
        terminals = terminals_resp.json()["all"]
        
        kl_sentral = next((t for t in terminals if "KL Sentral" in t["name"]), None)
        sungai_nibong = next((t for t in terminals if "Sungai Nibong" in t["name"]), None)
        
        if not kl_sentral or not sungai_nibong:
            pytest.skip("Required terminals not found")
        
        # Search for schedules
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        search_resp = api_client.get(f"{API_URL}/search", params={
            "from_terminal_id": kl_sentral["id"],
            "to_terminal_id": sungai_nibong["id"],
            "date": tomorrow
        })
        schedules = search_resp.json().get("schedules", [])
        
        # Find schedule with available seats
        for sched in schedules:
            if sched["seats_available"] >= 4:
                return sched
        
        pytest.skip("No schedule with enough available seats")
    
    def test_lock_seats_success(self, api_client, available_schedule):
        """POST /api/seats/lock successfully locks available seats"""
        schedule_id = available_schedule["id"]
        
        # Get available seats
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule_id}")
        layout = sched_resp.json()["layout"]
        
        # Find 2 available seats
        available_seats = []
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seats.append(cell["seat_number"])
                    if len(available_seats) >= 2:
                        break
            if len(available_seats) >= 2:
                break
        
        if len(available_seats) < 2:
            pytest.skip("Not enough available seats")
        
        # Lock the seats
        response = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule_id,
            "seat_numbers": available_seats
        })
        assert response.status_code == 200
        data = response.json()
        
        assert "lock_token" in data
        assert "expires_at" in data
        assert "seats" in data
        assert set(data["seats"]) == set(available_seats)
        
        print(f"Successfully locked seats: {available_seats}")
        return {"lock_token": data["lock_token"], "seats": available_seats, "schedule_id": schedule_id}
    
    def test_double_booking_protection_returns_409(self, api_client, available_schedule):
        """POST /api/seats/lock twice with overlapping seats: second must return 409 with failed_seats"""
        schedule_id = available_schedule["id"]
        
        # Get available seats
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule_id}")
        layout = sched_resp.json()["layout"]
        
        # Find 2 available seats
        available_seats = []
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seats.append(cell["seat_number"])
                    if len(available_seats) >= 2:
                        break
            if len(available_seats) >= 2:
                break
        
        if len(available_seats) < 2:
            pytest.skip("Not enough available seats")
        
        # First lock - should succeed
        first_response = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule_id,
            "seat_numbers": available_seats
        })
        assert first_response.status_code == 200
        first_lock = first_response.json()
        print(f"First lock succeeded: {available_seats}")
        
        # Second lock with SAME seats - should fail with 409
        second_response = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule_id,
            "seat_numbers": available_seats
        })
        
        assert second_response.status_code == 409, f"Expected 409, got {second_response.status_code}"
        detail = second_response.json().get("detail", {})
        
        # Verify failed_seats in response
        assert "failed_seats" in detail, f"Response should contain failed_seats: {detail}"
        assert set(detail["failed_seats"]) == set(available_seats)
        
        print(f"Double-booking protection working! Failed seats: {detail['failed_seats']}")
        
        # Cleanup: release first lock
        api_client.post(f"{API_URL}/seats/release", params={"lock_token": first_lock["lock_token"]})


class TestBookingsAndPayments:
    """Booking creation and payment tests"""
    
    @pytest.fixture(scope="class")
    def booking_setup(self, api_client):
        """Setup: get schedule and lock seats for booking"""
        # Get terminals
        terminals_resp = api_client.get(f"{API_URL}/terminals")
        terminals = terminals_resp.json()["all"]
        
        kl_sentral = next((t for t in terminals if "KL Sentral" in t["name"]), None)
        sungai_nibong = next((t for t in terminals if "Sungai Nibong" in t["name"]), None)
        
        if not kl_sentral or not sungai_nibong:
            pytest.skip("Required terminals not found")
        
        # Search for schedules
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        search_resp = api_client.get(f"{API_URL}/search", params={
            "from_terminal_id": kl_sentral["id"],
            "to_terminal_id": sungai_nibong["id"],
            "date": tomorrow
        })
        schedules = search_resp.json().get("schedules", [])
        
        # Find schedule with available seats
        schedule = None
        for sched in schedules:
            if sched["seats_available"] >= 2:
                schedule = sched
                break
        
        if not schedule:
            pytest.skip("No schedule with enough available seats")
        
        # Get available seats
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seats = []
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seats.append(cell["seat_number"])
                    if len(available_seats) >= 2:
                        break
            if len(available_seats) >= 2:
                break
        
        if len(available_seats) < 2:
            pytest.skip("Not enough available seats")
        
        # Lock seats
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": available_seats
        })
        
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seats: {lock_resp.text}")
        
        lock_data = lock_resp.json()
        
        return {
            "schedule": schedule,
            "seats": available_seats,
            "lock_token": lock_data["lock_token"]
        }
    
    def test_create_booking_with_pricing(self, api_client, booking_setup):
        """POST /api/bookings creates booking with correct pricing (adult RM55 + child RM27.50 = RM82.50)"""
        schedule = booking_setup["schedule"]
        seats = booking_setup["seats"]
        
        # Create booking with 1 adult + 1 child
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [
                {"seat_number": seats[0], "passenger_index": 0},
                {"seat_number": seats[1], "passenger_index": 1}
            ],
            "passengers": [
                {"name": "TEST Adult Passenger", "category": "adult", "ic_or_passport": "A12345678"},
                {"name": "TEST Child Passenger", "category": "child", "ic_or_passport": "C12345678"}
            ],
            "contact_email": "test_booking@example.com",
            "contact_phone": "+60123456789"
        }
        
        response = api_client.post(f"{API_URL}/bookings", json=booking_body)
        assert response.status_code == 200, f"Booking failed: {response.text}"
        data = response.json()
        
        # Verify booking structure
        assert "id" in data
        assert "reference" in data
        assert "pricing" in data
        assert "status" in data
        assert data["status"] == "pending_payment"
        
        # Verify pricing calculation
        pricing = data["pricing"]
        adult_fare = pricing["adult_fare"]
        child_fare = pricing["child_fare"]
        
        # Child fare should be 50% of adult
        assert child_fare == round(adult_fare * 0.5, 2), f"Child fare should be 50% of adult: {child_fare} vs {adult_fare * 0.5}"
        
        # Total should be adult + child
        expected_total = round(adult_fare + child_fare, 2)
        assert pricing["total"] == expected_total, f"Total mismatch: {pricing['total']} vs {expected_total}"
        
        print(f"Booking created: {data['reference']}, Total: {pricing['currency'].upper()} {pricing['total']}")
        print(f"Pricing: Adult={adult_fare}, Child={child_fare}, Total={pricing['total']}")
        
        return data
    
    def test_create_checkout_session(self, api_client, booking_setup):
        """POST /api/payments/checkout creates Stripe session, returns url+session_id"""
        # First create a booking
        schedule = booking_setup["schedule"]
        seats = booking_setup["seats"]
        
        # Lock new seats for this test
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seats = []
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seats.append(cell["seat_number"])
                    if len(available_seats) >= 1:
                        break
            if len(available_seats) >= 1:
                break
        
        if not available_seats:
            pytest.skip("No available seats for checkout test")
        
        # Lock seat
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": available_seats
        })
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seats: {lock_resp.text}")
        
        # Create booking
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [{"seat_number": available_seats[0], "passenger_index": 0}],
            "passengers": [{"name": "TEST Checkout User", "category": "adult"}],
            "contact_email": "test_checkout@example.com",
            "contact_phone": "+60123456789"
        }
        
        booking_resp = api_client.post(f"{API_URL}/bookings", json=booking_body)
        if booking_resp.status_code != 200:
            pytest.skip(f"Could not create booking: {booking_resp.text}")
        
        booking = booking_resp.json()
        
        # Create checkout session
        checkout_resp = api_client.post(f"{API_URL}/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://example.com"
        })
        
        assert checkout_resp.status_code == 200, f"Checkout failed: {checkout_resp.text}"
        checkout_data = checkout_resp.json()
        
        assert "url" in checkout_data
        assert "session_id" in checkout_data
        assert checkout_data["url"].startswith("http")
        
        print(f"Checkout session created: {checkout_data['session_id']}")
        print(f"Redirect URL: {checkout_data['url'][:80]}...")
        
        return checkout_data
    
    def test_payment_status_endpoint(self, api_client):
        """GET /api/payments/status/{session_id} returns payment status"""
        # Use a non-existent session to test 404
        response = api_client.get(f"{API_URL}/payments/status/nonexistent_session_123")
        assert response.status_code == 404
        print("Payment status correctly returns 404 for unknown session")


class TestAdminEndpoints:
    """Admin-only endpoint tests"""
    
    def test_admin_stats_with_admin_auth(self, api_client, admin_token):
        """GET /api/admin/stats (admin auth required) returns counts"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"{API_URL}/admin/stats", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "users" in data
        assert "bookings" in data
        assert "confirmed_bookings" in data
        assert "terminals" in data
        assert "schedules" in data
        
        assert isinstance(data["users"], int)
        assert isinstance(data["bookings"], int)
        assert isinstance(data["terminals"], int)
        assert isinstance(data["schedules"], int)
        
        print(f"Admin stats: {data}")
    
    def test_admin_stats_without_auth_returns_401(self, api_client):
        """GET /api/admin/stats without auth returns 401"""
        fresh_session = requests.Session()
        response = fresh_session.get(f"{API_URL}/admin/stats")
        assert response.status_code == 401
        print("Admin stats correctly requires authentication")
    
    def test_admin_stats_non_admin_returns_403(self, api_client, test_user_data):
        """GET /api/admin/stats with non-admin user returns 403"""
        if "token" not in test_user_data:
            pytest.skip("No test user token available")
        
        headers = {"Authorization": f"Bearer {test_user_data['token']}"}
        response = api_client.get(f"{API_URL}/admin/stats", headers=headers)
        
        assert response.status_code == 403
        print("Admin stats correctly rejects non-admin users")


class TestBookingsMe:
    """User bookings endpoint tests"""
    
    def test_bookings_me_requires_auth(self, api_client):
        """GET /api/bookings/me requires authentication"""
        fresh_session = requests.Session()
        response = fresh_session.get(f"{API_URL}/bookings/me")
        assert response.status_code == 401
        print("Bookings/me correctly requires authentication")
    
    def test_bookings_me_with_auth(self, api_client, admin_token):
        """GET /api/bookings/me (auth required) returns user's bookings"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"{API_URL}/bookings/me", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"Bookings/me returned {len(data)} bookings")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
