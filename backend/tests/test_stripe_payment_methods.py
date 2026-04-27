"""
Test Suite: Stripe Payment Methods (Card, GrabPay, FPX)
Tests the replacement of iPay88 with 3 Stripe-native payment methods.
- Card: universal (all currencies)
- GrabPay: MY + SG (MYR, SGD)
- FPX: MY only (MYR only)
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# ============ FIXTURES ============

@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def admin_token(api_client):
    """Get admin authentication token"""
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": "admin@starqistna.com",
        "password": "Admin@123"
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip("Admin authentication failed - skipping authenticated tests")


@pytest.fixture(scope="module")
def terminals(api_client):
    """Get all terminals for booking tests"""
    response = api_client.get(f"{BASE_URL}/api/terminals")
    assert response.status_code == 200
    return response.json()["all"]


def find_schedule_by_currency(api_client, terminals, target_currency):
    """Find a schedule with the target currency and available seats"""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    
    # For MYR: origin must be MY
    # For SGD: origin must be SG
    if target_currency == "myr":
        origin_country = "MY"
        dest_country = "SG"  # or MY
    else:  # sgd
        origin_country = "SG"
        dest_country = "MY"
    
    origin_terminals = [t for t in terminals if t.get("country") == origin_country]
    dest_terminals = [t for t in terminals if t.get("country") == dest_country]
    
    # Also try same-country routes for MYR
    if target_currency == "myr":
        dest_terminals = terminals  # Any destination
    
    for date in [tomorrow, day_after]:
        for from_t in origin_terminals:
            for to_t in dest_terminals:
                if from_t["id"] == to_t["id"]:
                    continue
                response = api_client.get(f"{BASE_URL}/api/search", params={
                    "from_terminal_id": from_t["id"],
                    "to_terminal_id": to_t["id"],
                    "date": date
                })
                if response.status_code == 200:
                    schedules = response.json().get("schedules", [])
                    for s in schedules:
                        if s.get("currency", "myr").lower() == target_currency and s.get("seats_available", 0) > 0:
                            return s
    return None


def create_booking_for_schedule(api_client, schedule):
    """Helper to create a booking for a given schedule"""
    if not schedule:
        return None
    
    schedule_id = schedule["id"]
    
    # Get schedule details to find available seats
    response = api_client.get(f"{BASE_URL}/api/schedules/{schedule_id}")
    if response.status_code != 200:
        return None
    
    layout = response.json()["layout"]
    
    # Find an available seat
    available_seat = None
    for row in layout:
        for seat in row:
            if seat.get("seat_number") and seat.get("status") == "available":
                available_seat = seat["seat_number"]
                break
        if available_seat:
            break
    
    if not available_seat:
        return None
    
    # Lock the seat
    lock_response = api_client.post(f"{BASE_URL}/api/seats/lock", json={
        "schedule_id": schedule_id,
        "seat_numbers": [available_seat]
    })
    if lock_response.status_code != 200:
        return None
    
    # Create booking with unique email
    import uuid
    unique_email = f"test_stripe_{uuid.uuid4().hex[:8]}@test.com"
    
    booking_response = api_client.post(f"{BASE_URL}/api/bookings", json={
        "schedule_id": schedule_id,
        "seat_assignments": [{"seat_number": available_seat, "passenger_index": 0}],
        "passengers": [{"name": "Test Passenger", "category": "adult"}],
        "contact_email": unique_email,
        "contact_phone": "+60123456789"
    })
    
    if booking_response.status_code == 200:
        return booking_response.json()
    return None


# ============ PAYMENT OPTIONS TESTS ============

class TestPaymentOptionsMYR:
    """Test GET /api/payments/options/{booking_id} for MYR bookings"""
    
    def test_myr_booking_returns_three_options(self, api_client, terminals):
        """MYR booking should return card, grabpay, fpx - all available"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.get(f"{BASE_URL}/api/payments/options/{booking['id']}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["currency"] == "myr"
        
        options = data["options"]
        option_ids = [o["id"] for o in options]
        
        # Must have exactly card, grabpay, fpx
        assert "card" in option_ids, "card option missing for MYR"
        assert "grabpay" in option_ids, "grabpay option missing for MYR"
        assert "fpx" in option_ids, "fpx option missing for MYR"
        
        # No iPay88
        assert "ipay88" not in option_ids, "ipay88 should not be present"
        
        # All should be available
        for opt in options:
            assert opt["available"], f"{opt['id']} should be available for MYR"
    
    def test_myr_options_have_correct_structure(self, api_client, terminals):
        """Each option should have id, name, provider, methods, available, note"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.get(f"{BASE_URL}/api/payments/options/{booking['id']}")
        assert response.status_code == 200
        
        for opt in response.json()["options"]:
            assert "id" in opt
            assert "name" in opt
            assert "provider" in opt
            assert "methods" in opt
            assert "available" in opt


class TestPaymentOptionsSGD:
    """Test GET /api/payments/options/{booking_id} for SGD bookings"""
    
    def test_sgd_booking_returns_two_options_no_fpx(self, api_client, terminals):
        """SGD booking should return card, grabpay - no fpx"""
        schedule = find_schedule_by_currency(api_client, terminals, "sgd")
        if not schedule:
            pytest.skip("No SGD schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create SGD booking"
        
        response = api_client.get(f"{BASE_URL}/api/payments/options/{booking['id']}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["currency"] == "sgd"
        
        options = data["options"]
        option_ids = [o["id"] for o in options]
        
        # Must have card and grabpay
        assert "card" in option_ids, "card option missing for SGD"
        assert "grabpay" in option_ids, "grabpay option missing for SGD"
        
        # No FPX for SGD
        assert "fpx" not in option_ids, "fpx should NOT be present for SGD"
        
        # No iPay88
        assert "ipay88" not in option_ids, "ipay88 should not be present"
        
        # card and grabpay should be available
        for opt in options:
            if opt["id"] in ["card", "grabpay"]:
                assert opt["available"], f"{opt['id']} should be available for SGD"


# ============ CHECKOUT TESTS ============

class TestCheckoutCard:
    """Test POST /api/payments/checkout with gateway='card'"""
    
    def test_card_checkout_myr_returns_stripe_url(self, api_client, terminals):
        """Card checkout for MYR booking should return Stripe URL"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "card"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "url" in data, "Response should contain 'url'"
        assert "session_id" in data, "Response should contain 'session_id'"
        assert "stripe.com" in data["url"] or "checkout" in data["url"].lower(), "URL should be Stripe checkout"
    
    def test_card_checkout_sgd_returns_stripe_url(self, api_client, terminals):
        """Card checkout for SGD booking should return Stripe URL"""
        schedule = find_schedule_by_currency(api_client, terminals, "sgd")
        if not schedule:
            pytest.skip("No SGD schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create SGD booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "card"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert "session_id" in data


class TestCheckoutGrabPay:
    """Test POST /api/payments/checkout with gateway='grabpay'"""
    
    def test_grabpay_checkout_myr_returns_stripe_url(self, api_client, terminals):
        """GrabPay checkout for MYR booking should return Stripe URL"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "grabpay"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert "session_id" in data
    
    def test_grabpay_checkout_sgd_returns_stripe_url(self, api_client, terminals):
        """GrabPay checkout for SGD booking should return Stripe URL"""
        schedule = find_schedule_by_currency(api_client, terminals, "sgd")
        if not schedule:
            pytest.skip("No SGD schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create SGD booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "grabpay"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert "session_id" in data


class TestCheckoutFPX:
    """Test POST /api/payments/checkout with gateway='fpx'"""
    
    def test_fpx_checkout_myr_returns_stripe_url(self, api_client, terminals):
        """FPX checkout for MYR booking should return Stripe URL"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "fpx"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert "session_id" in data
    
    def test_fpx_checkout_sgd_returns_400(self, api_client, terminals):
        """FPX checkout for SGD booking should return 400 error"""
        schedule = find_schedule_by_currency(api_client, terminals, "sgd")
        if not schedule:
            pytest.skip("No SGD schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create SGD booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "fpx"
        })
        
        assert response.status_code == 400, f"FPX for SGD should return 400, got {response.status_code}"
        data = response.json()
        assert "not available" in str(data).lower() or "fpx" in str(data).lower()


class TestCheckoutInvalidGateway:
    """Test POST /api/payments/checkout with invalid gateways"""
    
    def test_ipay88_gateway_returns_400(self, api_client, terminals):
        """Legacy iPay88 gateway should return 400 error"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "ipay88"
        })
        
        assert response.status_code == 400, f"iPay88 should return 400, got {response.status_code}"
    
    def test_bogus_gateway_returns_400(self, api_client, terminals):
        """Bogus gateway should return 400 error"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "bogus_method"
        })
        
        assert response.status_code == 400, f"Bogus gateway should return 400, got {response.status_code}"


class TestPaymentTransactionRecord:
    """Test that payment_transactions record is created with correct payment_method"""
    
    def test_card_checkout_creates_transaction_with_card_method(self, api_client, admin_token, terminals):
        """Card checkout should create payment_transactions with payment_method='card'"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        # Create checkout
        checkout_response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "card"
        })
        assert checkout_response.status_code == 200
        session_id = checkout_response.json()["session_id"]
        
        # Check admin payments endpoint for the transaction
        headers = {"Authorization": f"Bearer {admin_token}"}
        payments_response = api_client.get(f"{BASE_URL}/api/admin/payments", headers=headers)
        assert payments_response.status_code == 200
        
        items = payments_response.json()["items"]
        txn = next((t for t in items if t.get("session_id") == session_id), None)
        
        assert txn is not None, "Transaction not found in admin payments"
        assert txn.get("payment_method") == "card", f"Expected payment_method='card', got '{txn.get('payment_method')}'"
    
    def test_grabpay_checkout_creates_transaction_with_grabpay_method(self, api_client, admin_token, terminals):
        """GrabPay checkout should create payment_transactions with payment_method='grabpay'"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        checkout_response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "grabpay"
        })
        assert checkout_response.status_code == 200
        session_id = checkout_response.json()["session_id"]
        
        headers = {"Authorization": f"Bearer {admin_token}"}
        payments_response = api_client.get(f"{BASE_URL}/api/admin/payments", headers=headers)
        assert payments_response.status_code == 200
        
        items = payments_response.json()["items"]
        txn = next((t for t in items if t.get("session_id") == session_id), None)
        
        assert txn is not None, "Transaction not found"
        assert txn.get("payment_method") == "grabpay", f"Expected payment_method='grabpay', got '{txn.get('payment_method')}'"
    
    def test_fpx_checkout_creates_transaction_with_fpx_method(self, api_client, admin_token, terminals):
        """FPX checkout should create payment_transactions with payment_method='fpx'"""
        schedule = find_schedule_by_currency(api_client, terminals, "myr")
        if not schedule:
            pytest.skip("No MYR schedule with available seats found")
        
        booking = create_booking_for_schedule(api_client, schedule)
        assert booking is not None, "Failed to create MYR booking"
        
        checkout_response = api_client.post(f"{BASE_URL}/api/payments/checkout", json={
            "booking_id": booking["id"],
            "origin_url": "https://test.example.com",
            "gateway": "fpx"
        })
        assert checkout_response.status_code == 200
        session_id = checkout_response.json()["session_id"]
        
        headers = {"Authorization": f"Bearer {admin_token}"}
        payments_response = api_client.get(f"{BASE_URL}/api/admin/payments", headers=headers)
        assert payments_response.status_code == 200
        
        items = payments_response.json()["items"]
        txn = next((t for t in items if t.get("session_id") == session_id), None)
        
        assert txn is not None, "Transaction not found"
        assert txn.get("payment_method") == "fpx", f"Expected payment_method='fpx', got '{txn.get('payment_method')}'"


# ============ ADMIN PAYMENTS TAB REGRESSION ============

class TestAdminPaymentsRegression:
    """Regression test: Admin Payments tab still loads and displays transactions"""
    
    def test_admin_payments_tab_loads(self, api_client, admin_token):
        """Admin payments endpoint should return 200 with summary and items"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"{BASE_URL}/api/admin/payments", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "items" in data
        assert isinstance(data["items"], list)
    
    def test_admin_payments_summary_has_required_fields(self, api_client, admin_token):
        """Summary should have total, paid, initiated, failed, refunded, gross_myr, gross_sgd"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"{BASE_URL}/api/admin/payments", headers=headers)
        
        assert response.status_code == 200
        summary = response.json()["summary"]
        
        required_fields = ["total", "paid", "initiated", "failed", "refunded", "gross_myr", "gross_sgd"]
        for field in required_fields:
            assert field in summary, f"Summary missing field: {field}"
