"""
Test Admin Payments API - Iteration 6
Tests for GET /api/admin/payments endpoint with auth and filtering
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAdminPaymentsAuth:
    """Authentication and authorization tests for admin payments endpoint"""
    
    def test_payments_without_token_returns_401(self):
        """GET /api/admin/payments without token should return 401"""
        response = requests.get(f"{BASE_URL}/api/admin/payments")
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        print(f"✅ Without token: 401 - {data['detail']}")
    
    def test_payments_with_non_admin_token_returns_403(self):
        """GET /api/admin/payments with non-admin token should return 403"""
        # Register a regular user
        register_response = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": "test_nonadmin_payments@test.com",
                "password": "Test@123",
                "full_name": "Test Non-Admin"
            }
        )
        
        if register_response.status_code == 400:
            # User already exists, login instead
            login_response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": "test_nonadmin_payments@test.com",
                    "password": "Test@123"
                }
            )
            token = login_response.json().get("access_token")
        else:
            token = register_response.json().get("access_token")
        
        assert token is not None, "Failed to get token for non-admin user"
        
        # Try to access admin payments
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert "detail" in data
        print(f"✅ Non-admin user: 403 - {data['detail']}")
    
    def test_payments_with_admin_token_returns_200(self):
        """GET /api/admin/payments with admin token should return 200"""
        # Login as admin
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": "admin@starqistna.com",
                "password": "Admin@123"
            }
        )
        assert login_response.status_code == 200
        token = login_response.json().get("access_token")
        
        # Access admin payments
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        print(f"✅ Admin user: 200 OK")


class TestAdminPaymentsResponse:
    """Response structure tests for admin payments endpoint"""
    
    @pytest.fixture
    def admin_token(self):
        """Get admin token"""
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": "admin@starqistna.com",
                "password": "Admin@123"
            }
        )
        return login_response.json().get("access_token")
    
    def test_response_has_summary_and_items(self, admin_token):
        """Response should have summary and items keys"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        
        assert "summary" in data, "Response missing 'summary' key"
        assert "items" in data, "Response missing 'items' key"
        print(f"✅ Response has summary and items keys")
    
    def test_summary_has_required_keys(self, admin_token):
        """Summary should have total/paid/initiated/failed/refunded/gross_myr/gross_sgd"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        summary = response.json().get("summary", {})
        
        required_keys = ["total", "paid", "initiated", "failed", "refunded", "gross_myr", "gross_sgd"]
        for key in required_keys:
            assert key in summary, f"Summary missing '{key}' key"
        
        print(f"✅ Summary has all required keys: {required_keys}")
        print(f"   Summary values: total={summary['total']}, paid={summary['paid']}, initiated={summary['initiated']}, failed={summary['failed']}, refunded={summary['refunded']}")
        print(f"   Gross: MYR={summary['gross_myr']}, SGD={summary['gross_sgd']}")
    
    def test_items_is_list(self, admin_token):
        """Items should be a list"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        items = response.json().get("items", None)
        
        assert isinstance(items, list), "Items should be a list"
        print(f"✅ Items is a list with {len(items)} transactions")
    
    def test_item_has_required_fields(self, admin_token):
        """Each item should have required fields"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        items = response.json().get("items", [])
        
        if len(items) > 0:
            item = items[0]
            required_fields = ["session_id", "booking_id", "amount", "currency", "user_email", "payment_status", "created_at"]
            for field in required_fields:
                assert field in item, f"Item missing '{field}' field"
            print(f"✅ Items have required fields: {required_fields}")
        else:
            print("⚠️ No items to verify fields")


class TestAdminPaymentsFiltering:
    """Filter functionality tests for admin payments endpoint"""
    
    @pytest.fixture
    def admin_token(self):
        """Get admin token"""
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": "admin@starqistna.com",
                "password": "Admin@123"
            }
        )
        return login_response.json().get("access_token")
    
    def test_filter_paid_returns_only_paid(self, admin_token):
        """status_filter=paid should return only paid transactions"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments?status_filter=paid",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        items = data.get("items", [])
        
        for item in items:
            assert item.get("payment_status") == "paid", f"Expected paid status, got {item.get('payment_status')}"
        
        print(f"✅ Filter 'paid' returns {len(items)} paid transactions")
    
    def test_filter_initiated_returns_only_initiated(self, admin_token):
        """status_filter=initiated should return only initiated transactions"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments?status_filter=initiated",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        items = data.get("items", [])
        
        for item in items:
            assert item.get("payment_status") == "initiated", f"Expected initiated status, got {item.get('payment_status')}"
        
        print(f"✅ Filter 'initiated' returns {len(items)} initiated transactions")
    
    def test_filter_all_returns_all(self, admin_token):
        """status_filter=all should return all transactions (same as no filter)"""
        # Get with filter=all
        response_all = requests.get(
            f"{BASE_URL}/api/admin/payments?status_filter=all",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        items_all = response_all.json().get("items", [])
        
        # Get without filter
        response_none = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        items_none = response_none.json().get("items", [])
        
        assert len(items_all) == len(items_none), f"Filter 'all' ({len(items_all)}) should match no filter ({len(items_none)})"
        print(f"✅ Filter 'all' returns same count as no filter: {len(items_all)} transactions")
    
    def test_filter_failed_returns_only_failed(self, admin_token):
        """status_filter=failed should return only failed transactions"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments?status_filter=failed",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        items = data.get("items", [])
        
        for item in items:
            assert item.get("payment_status") == "failed", f"Expected failed status, got {item.get('payment_status')}"
        
        print(f"✅ Filter 'failed' returns {len(items)} failed transactions")


class TestAdminPaymentsSummaryAccuracy:
    """Summary calculation accuracy tests"""
    
    @pytest.fixture
    def admin_token(self):
        """Get admin token"""
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": "admin@starqistna.com",
                "password": "Admin@123"
            }
        )
        return login_response.json().get("access_token")
    
    def test_summary_total_matches_items_count(self, admin_token):
        """Summary total should match items count"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        summary = data.get("summary", {})
        items = data.get("items", [])
        
        assert summary.get("total") == len(items), f"Summary total ({summary.get('total')}) doesn't match items count ({len(items)})"
        print(f"✅ Summary total ({summary.get('total')}) matches items count")
    
    def test_summary_paid_count_accurate(self, admin_token):
        """Summary paid count should match actual paid items"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        summary = data.get("summary", {})
        items = data.get("items", [])
        
        actual_paid = sum(1 for item in items if item.get("payment_status") == "paid")
        assert summary.get("paid") == actual_paid, f"Summary paid ({summary.get('paid')}) doesn't match actual ({actual_paid})"
        print(f"✅ Summary paid count ({summary.get('paid')}) is accurate")
    
    def test_gross_myr_calculation(self, admin_token):
        """Gross MYR should be sum of paid MYR transactions"""
        response = requests.get(
            f"{BASE_URL}/api/admin/payments",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        data = response.json()
        summary = data.get("summary", {})
        items = data.get("items", [])
        
        actual_gross_myr = sum(
            float(item.get("amount", 0))
            for item in items
            if item.get("payment_status") == "paid" and (item.get("currency") or "myr").lower() == "myr"
        )
        
        assert abs(summary.get("gross_myr", 0) - actual_gross_myr) < 0.01, \
            f"Gross MYR ({summary.get('gross_myr')}) doesn't match calculated ({actual_gross_myr})"
        print(f"✅ Gross MYR ({summary.get('gross_myr')}) is accurate")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
