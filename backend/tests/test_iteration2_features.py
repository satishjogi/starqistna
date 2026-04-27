"""
Transit E1 - Iteration 2 Features Tests
Tests: Promo Codes (validate, admin CRUD), Boarding Validation (QR gate), Round-trip upsell support
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
API_URL = f"{BASE_URL}/api"

# Test credentials
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
def test_user_token(api_client):
    """Create and get a non-admin user token"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    user_data = {
        "email": f"TEST_promo_user_{timestamp}@example.com",
        "password": "TestPass123",
        "full_name": f"TEST Promo User {timestamp}",
        "phone": "+60123456789"
    }
    response = api_client.post(f"{API_URL}/auth/register", json=user_data)
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"User registration failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def schedule_data(api_client):
    """Get a valid schedule for testing"""
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
    
    if not schedules:
        pytest.skip("No schedules available")
    
    return {
        "schedule": schedules[0],
        "from_terminal": kl_sentral,
        "to_terminal": sungai_nibong
    }


# ============ PROMO CODE VALIDATION TESTS ============

class TestPromoValidation:
    """Tests for POST /api/promo/validate endpoint"""
    
    def test_validate_promo_welcome10_returns_10_percent_discount(self, api_client, schedule_data):
        """POST /api/promo/validate with WELCOME10 returns 10% discount"""
        schedule = schedule_data["schedule"]
        
        response = api_client.post(f"{API_URL}/promo/validate", json={
            "code": "WELCOME10",
            "schedule_id": schedule["id"],
            "passenger_count": 2,
            "adults": 1,
            "children": 1
        })
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert data["valid"]
        assert "promo" in data
        assert data["promo"]["code"] == "WELCOME10"
        assert data["promo"]["type"] == "percent"
        assert data["promo"]["value"] == 10
        assert "discount_amount" in data["promo"]
        
        # Verify discount calculation
        pricing = data["pricing"]
        expected_discount = round(pricing["subtotal"] * 0.10, 2)
        assert data["promo"]["discount_amount"] == expected_discount
        
        print(f"WELCOME10 validated: subtotal={pricing['subtotal']}, discount={data['promo']['discount_amount']}, total={pricing['total']}")
    
    def test_validate_promo_raya5_returns_flat_5_discount(self, api_client, schedule_data):
        """POST /api/promo/validate with RAYA5 returns flat RM5 discount"""
        schedule = schedule_data["schedule"]
        
        response = api_client.post(f"{API_URL}/promo/validate", json={
            "code": "RAYA5",
            "schedule_id": schedule["id"],
            "passenger_count": 1,
            "adults": 1,
            "children": 0
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["valid"]
        assert data["promo"]["code"] == "RAYA5"
        assert data["promo"]["type"] == "flat"
        assert data["promo"]["value"] == 5
        assert data["promo"]["discount_amount"] == 5.0
        
        print("RAYA5 validated: flat RM5 discount applied")
    
    def test_validate_promo_nonexistent_code_returns_404(self, api_client, schedule_data):
        """POST /api/promo/validate with non-existent code returns 404"""
        schedule = schedule_data["schedule"]
        
        response = api_client.post(f"{API_URL}/promo/validate", json={
            "code": "NONEXISTENT_CODE_XYZ",
            "schedule_id": schedule["id"],
            "passenger_count": 1,
            "adults": 1,
            "children": 0
        })
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        print("Non-existent promo code correctly returns 404")
    
    def test_validate_promo_case_insensitive(self, api_client, schedule_data):
        """POST /api/promo/validate normalizes code to uppercase"""
        schedule = schedule_data["schedule"]
        
        response = api_client.post(f"{API_URL}/promo/validate", json={
            "code": "welcome10",  # lowercase
            "schedule_id": schedule["id"],
            "passenger_count": 1,
            "adults": 1,
            "children": 0
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["promo"]["code"] == "WELCOME10"  # Should be normalized
        print("Promo code correctly normalized to uppercase")


# ============ PROMO CODE ADMIN CRUD TESTS ============

class TestPromoAdminCRUD:
    """Tests for admin promo code CRUD endpoints"""
    
    def test_list_promo_codes_returns_seeded_codes(self, api_client, admin_token):
        """GET /api/admin/promo-codes returns seeded codes (WELCOME10, RAYA5)"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"{API_URL}/admin/promo-codes", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, list)
        codes = [p["code"] for p in data]
        
        assert "WELCOME10" in codes, f"WELCOME10 not found in {codes}"
        assert "RAYA5" in codes, f"RAYA5 not found in {codes}"
        
        # Verify structure
        for promo in data:
            assert "id" in promo
            assert "code" in promo
            assert "type" in promo
            assert "value" in promo
            assert "active" in promo
            assert "used_count" in promo
        
        print(f"Found {len(data)} promo codes: {codes}")
    
    def test_create_promo_code_success(self, api_client, admin_token):
        """POST /api/admin/promo-codes creates new code"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        timestamp = datetime.now().strftime("%H%M%S")
        new_code = f"TEST{timestamp}"
        
        response = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": new_code,
            "type": "percent",
            "value": 15,
            "currency": "myr",
            "description": "Test promo code"
        })
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert data["code"] == new_code.upper()
        assert data["type"] == "percent"
        assert data["value"] == 15
        assert data["active"]
        assert data["used_count"] == 0
        assert "id" in data
        
        print(f"Created promo code: {data['code']}")
        return data
    
    def test_create_duplicate_promo_code_returns_400(self, api_client, admin_token):
        """POST /api/admin/promo-codes with duplicate code returns 400"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        
        response = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": "WELCOME10",  # Already exists
            "type": "percent",
            "value": 20,
            "currency": "myr"
        })
        
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        print("Duplicate promo code correctly rejected with 400")
    
    def test_toggle_promo_code_active_off(self, api_client, admin_token):
        """PATCH /api/admin/promo-codes/{id}?active=false toggles off"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        
        # First create a code to toggle
        timestamp = datetime.now().strftime("%H%M%S%f")
        new_code = f"TOGGLE{timestamp}"
        
        create_resp = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": new_code,
            "type": "flat",
            "value": 3,
            "currency": "myr"
        })
        assert create_resp.status_code == 200
        code_id = create_resp.json()["id"]
        
        # Toggle off
        toggle_resp = api_client.patch(f"{API_URL}/admin/promo-codes/{code_id}?active=false", headers=headers)
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["updated"]
        
        # Verify it's off
        list_resp = api_client.get(f"{API_URL}/admin/promo-codes", headers=headers)
        codes = list_resp.json()
        toggled = next((c for c in codes if c["id"] == code_id), None)
        assert toggled is not None
        assert not toggled["active"]
        
        print(f"Promo code {new_code} toggled off successfully")
        return code_id
    
    def test_delete_promo_code(self, api_client, admin_token):
        """DELETE /api/admin/promo-codes/{id} removes code"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        
        # First create a code to delete
        timestamp = datetime.now().strftime("%H%M%S%f")
        new_code = f"DELETE{timestamp}"
        
        create_resp = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": new_code,
            "type": "flat",
            "value": 2,
            "currency": "myr"
        })
        assert create_resp.status_code == 200
        code_id = create_resp.json()["id"]
        
        # Delete
        delete_resp = api_client.delete(f"{API_URL}/admin/promo-codes/{code_id}", headers=headers)
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"]
        
        # Verify it's gone
        list_resp = api_client.get(f"{API_URL}/admin/promo-codes", headers=headers)
        codes = list_resp.json()
        deleted = next((c for c in codes if c["id"] == code_id), None)
        assert deleted is None
        
        print(f"Promo code {new_code} deleted successfully")
    
    def test_promo_crud_non_admin_returns_403(self, api_client, test_user_token):
        """Non-admin on promo CRUD returns 403"""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        # Try to list
        list_resp = api_client.get(f"{API_URL}/admin/promo-codes", headers=headers)
        assert list_resp.status_code == 403, f"Expected 403, got {list_resp.status_code}"
        
        # Try to create
        create_resp = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": "HACKER",
            "type": "percent",
            "value": 100,
            "currency": "myr"
        })
        assert create_resp.status_code == 403
        
        print("Non-admin correctly rejected from promo CRUD with 403")


# ============ BOOKING WITH PROMO CODE TESTS ============

class TestBookingWithPromo:
    """Tests for booking creation with promo codes"""
    
    @pytest.fixture(scope="class")
    def booking_setup(self, api_client, schedule_data):
        """Setup: lock seats for booking"""
        schedule = schedule_data["schedule"]
        
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
        
        return {
            "schedule": schedule,
            "seats": available_seats,
            "lock_token": lock_resp.json()["lock_token"]
        }
    
    def test_booking_with_welcome10_applies_discount(self, api_client, booking_setup):
        """POST /api/bookings with WELCOME10 applies 10% discount server-side"""
        schedule = booking_setup["schedule"]
        seats = booking_setup["seats"]
        
        # Create booking with 1 adult + 1 child + WELCOME10
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [
                {"seat_number": seats[0], "passenger_index": 0},
                {"seat_number": seats[1], "passenger_index": 1}
            ],
            "passengers": [
                {"name": "TEST Adult Promo", "category": "adult", "ic_or_passport": "A12345678"},
                {"name": "TEST Child Promo", "category": "child", "ic_or_passport": "C12345678"}
            ],
            "contact_email": "test_promo_booking@example.com",
            "contact_phone": "+60123456789",
            "promo_code": "WELCOME10"
        }
        
        response = api_client.post(f"{API_URL}/bookings", json=booking_body)
        assert response.status_code == 200, f"Booking failed: {response.text}"
        data = response.json()
        
        pricing = data["pricing"]
        
        # Verify promo was applied
        assert pricing["promo"] is not None, "Promo should be applied"
        assert pricing["promo"]["code"] == "WELCOME10"
        assert pricing["promo"]["type"] == "percent"
        assert pricing["promo"]["value"] == 10
        
        # Verify discount calculation
        # subtotal = adult_fare + child_fare (50% of adult)
        adult_fare = pricing["adult_fare"]
        child_fare = pricing["child_fare"]
        expected_subtotal = round(adult_fare + child_fare, 2)
        expected_discount = round(expected_subtotal * 0.10, 2)
        expected_total = round(expected_subtotal - expected_discount, 2)
        
        assert pricing["subtotal"] == expected_subtotal, f"Subtotal mismatch: {pricing['subtotal']} vs {expected_subtotal}"
        assert pricing["discount"] == expected_discount, f"Discount mismatch: {pricing['discount']} vs {expected_discount}"
        assert pricing["total"] == expected_total, f"Total mismatch: {pricing['total']} vs {expected_total}"
        
        print(f"Booking with WELCOME10: subtotal={pricing['subtotal']}, discount={pricing['discount']}, total={pricing['total']}")
        print(f"Reference: {data['reference']}")
        
        return data
    
    def test_booking_with_invalid_promo_returns_404(self, api_client, schedule_data):
        """POST /api/bookings with invalid promo_code rejects (404)"""
        schedule = schedule_data["schedule"]
        
        # Get and lock a seat
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seat = None
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seat = cell["seat_number"]
                    break
            if available_seat:
                break
        
        if not available_seat:
            pytest.skip("No available seats")
        
        # Lock seat
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": [available_seat]
        })
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seat: {lock_resp.text}")
        
        # Try booking with invalid promo
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [{"seat_number": available_seat, "passenger_index": 0}],
            "passengers": [{"name": "TEST Invalid Promo", "category": "adult"}],
            "contact_email": "test_invalid_promo@example.com",
            "contact_phone": "+60123456789",
            "promo_code": "INVALID_CODE_XYZ"
        }
        
        response = api_client.post(f"{API_URL}/bookings", json=booking_body)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        
        print("Booking with invalid promo correctly rejected with 404")


# ============ BOARDING VALIDATION TESTS ============

class TestBoardingValidation:
    """Tests for POST /api/boarding/validate endpoint (QR gate readers)"""
    
    def test_boarding_validate_unknown_reference_returns_not_found(self, api_client):
        """POST /api/boarding/validate with unknown reference -> {valid:false, reason:not_found}"""
        response = api_client.post(f"{API_URL}/boarding/validate", json={
            "reference": "UNKNOWN_REF_XYZ123",
            "gate": "A1"
        })
        
        assert response.status_code == 200  # Returns 200 with valid:false
        data = response.json()
        
        assert not data["valid"]
        assert data["reason"] == "not_found"
        
        print("Unknown reference correctly returns valid:false, reason:not_found")
    
    def test_boarding_validate_unconfirmed_booking_returns_not_confirmed(self, api_client, schedule_data):
        """POST /api/boarding/validate on unconfirmed booking -> {valid:false, reason:not_confirmed}"""
        schedule = schedule_data["schedule"]
        
        # Create a booking (will be pending_payment)
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seat = None
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seat = cell["seat_number"]
                    break
            if available_seat:
                break
        
        if not available_seat:
            pytest.skip("No available seats")
        
        # Lock and create booking
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": [available_seat]
        })
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seat: {lock_resp.text}")
        
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [{"seat_number": available_seat, "passenger_index": 0}],
            "passengers": [{"name": "TEST Unconfirmed Boarding", "category": "adult"}],
            "contact_email": "test_unconfirmed@example.com",
            "contact_phone": "+60123456789"
        }
        
        booking_resp = api_client.post(f"{API_URL}/bookings", json=booking_body)
        if booking_resp.status_code != 200:
            pytest.skip(f"Could not create booking: {booking_resp.text}")
        
        booking = booking_resp.json()
        
        # Try to validate boarding (should fail - not confirmed)
        validate_resp = api_client.post(f"{API_URL}/boarding/validate", json={
            "reference": booking["reference"],
            "gate": "A1"
        })
        
        assert validate_resp.status_code == 200
        data = validate_resp.json()
        
        assert not data["valid"]
        assert data["reason"] == "not_confirmed"
        assert data["status"] == "pending_payment"
        
        print(f"Unconfirmed booking {booking['reference']} correctly returns valid:false, reason:not_confirmed")
        return booking
    
    def test_boarding_validate_confirmed_booking_returns_passenger_info(self, api_client, schedule_data):
        """POST /api/boarding/validate with valid confirmed reference returns passenger + seat info"""
        schedule = schedule_data["schedule"]
        
        # Create a booking
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seat = None
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seat = cell["seat_number"]
                    break
            if available_seat:
                break
        
        if not available_seat:
            pytest.skip("No available seats")
        
        # Lock and create booking
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": [available_seat]
        })
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seat: {lock_resp.text}")
        
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [{"seat_number": available_seat, "passenger_index": 0}],
            "passengers": [{"name": "TEST Confirmed Boarding", "category": "adult", "ic_or_passport": "A99999999"}],
            "contact_email": "test_confirmed@example.com",
            "contact_phone": "+60123456789"
        }
        
        booking_resp = api_client.post(f"{API_URL}/bookings", json=booking_body)
        if booking_resp.status_code != 200:
            pytest.skip(f"Could not create booking: {booking_resp.text}")
        
        booking = booking_resp.json()
        reference = booking["reference"]
        
        # Force-confirm the booking via MongoDB
        import subprocess
        mongo_cmd = f'''python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017')['test_database'].bookings.update_one({{'reference': '{reference}'}}, {{'\\$set': {{'status':'confirmed','payment_status':'paid'}}}})"'''
        subprocess.run(mongo_cmd, shell=True, capture_output=True, text=True)
        
        # Validate boarding
        validate_resp = api_client.post(f"{API_URL}/boarding/validate", json={
            "reference": reference,
            "gate": "A1",
            "mark_boarded": True
        })
        
        assert validate_resp.status_code == 200
        data = validate_resp.json()
        
        assert data["valid"]
        assert data["reference"] == reference
        assert "passengers" in data
        assert "seats" in data
        assert "schedule" in data
        assert not data["already_boarded"]
        assert "boarded_at" in data
        
        # Verify passenger info
        assert len(data["passengers"]) == 1
        assert data["passengers"][0]["name"] == "TEST Confirmed Boarding"
        assert data["seats"] == [available_seat]
        
        print(f"Confirmed booking {reference} validated successfully with passenger info")
        return reference
    
    def test_boarding_validate_idempotent_second_call_returns_already_boarded(self, api_client, schedule_data):
        """Second call to boarding/validate returns already_boarded:true (idempotent)"""
        schedule = schedule_data["schedule"]
        
        # Create and confirm a booking
        sched_resp = api_client.get(f"{API_URL}/schedules/{schedule['id']}")
        layout = sched_resp.json()["layout"]
        
        available_seat = None
        for row in layout:
            for cell in row:
                if not cell.get("aisle") and cell.get("status") == "available":
                    available_seat = cell["seat_number"]
                    break
            if available_seat:
                break
        
        if not available_seat:
            pytest.skip("No available seats")
        
        # Lock and create booking
        lock_resp = api_client.post(f"{API_URL}/seats/lock", json={
            "schedule_id": schedule["id"],
            "seat_numbers": [available_seat]
        })
        if lock_resp.status_code != 200:
            pytest.skip(f"Could not lock seat: {lock_resp.text}")
        
        booking_body = {
            "schedule_id": schedule["id"],
            "seat_assignments": [{"seat_number": available_seat, "passenger_index": 0}],
            "passengers": [{"name": "TEST Idempotent Boarding", "category": "adult"}],
            "contact_email": "test_idempotent@example.com",
            "contact_phone": "+60123456789"
        }
        
        booking_resp = api_client.post(f"{API_URL}/bookings", json=booking_body)
        if booking_resp.status_code != 200:
            pytest.skip(f"Could not create booking: {booking_resp.text}")
        
        booking = booking_resp.json()
        reference = booking["reference"]
        
        # Force-confirm the booking
        import subprocess
        mongo_cmd = f'''python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017')['test_database'].bookings.update_one({{'reference': '{reference}'}}, {{'\\$set': {{'status':'confirmed','payment_status':'paid'}}}})"'''
        subprocess.run(mongo_cmd, shell=True, capture_output=True, text=True)
        
        # First validation
        first_resp = api_client.post(f"{API_URL}/boarding/validate", json={
            "reference": reference,
            "gate": "A1",
            "mark_boarded": True
        })
        assert first_resp.status_code == 200
        first_data = first_resp.json()
        assert first_data["valid"]
        assert not first_data["already_boarded"]
        
        # Second validation (should be idempotent)
        second_resp = api_client.post(f"{API_URL}/boarding/validate", json={
            "reference": reference,
            "gate": "A1",
            "mark_boarded": True
        })
        assert second_resp.status_code == 200
        second_data = second_resp.json()
        
        assert second_data["valid"]
        assert second_data["already_boarded"]
        
        print("Boarding validation is idempotent - second call returns already_boarded:true")


# ============ PROMO INACTIVE CODE TEST ============

class TestPromoInactiveCode:
    """Test that inactive promo codes are rejected"""
    
    def test_validate_inactive_promo_returns_400(self, api_client, admin_token, schedule_data):
        """POST /api/promo/validate with inactive code returns 400"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        schedule = schedule_data["schedule"]
        
        # Create a code and deactivate it
        timestamp = datetime.now().strftime("%H%M%S%f")
        new_code = f"INACTIVE{timestamp}"
        
        create_resp = api_client.post(f"{API_URL}/admin/promo-codes", headers=headers, json={
            "code": new_code,
            "type": "percent",
            "value": 5,
            "currency": "myr"
        })
        assert create_resp.status_code == 200
        code_id = create_resp.json()["id"]
        
        # Deactivate
        toggle_resp = api_client.patch(f"{API_URL}/admin/promo-codes/{code_id}?active=false", headers=headers)
        assert toggle_resp.status_code == 200
        
        # Try to validate
        validate_resp = api_client.post(f"{API_URL}/promo/validate", json={
            "code": new_code,
            "schedule_id": schedule["id"],
            "passenger_count": 1,
            "adults": 1,
            "children": 0
        })
        
        assert validate_resp.status_code == 400, f"Expected 400, got {validate_resp.status_code}: {validate_resp.text}"
        assert "inactive" in validate_resp.json().get("detail", "").lower()
        
        print(f"Inactive promo code {new_code} correctly rejected with 400")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
