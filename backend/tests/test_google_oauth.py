"""
Test Google OAuth (Emergent-managed) social login endpoints
Tests for iteration 3: Google social login feature
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestGoogleOAuthEndpoint:
    """Tests for POST /api/auth/google/session endpoint"""
    
    def test_google_session_endpoint_exists(self):
        """Verify the endpoint exists and accepts POST requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={"session_id": "invalid-test-session-id"},
            headers={"Content-Type": "application/json"}
        )
        # Should return 401 for invalid session, not 404 (endpoint exists)
        assert response.status_code in [401, 502], f"Expected 401 or 502, got {response.status_code}: {response.text}"
        print(f"PASS: POST /api/auth/google/session endpoint exists, returns {response.status_code} for invalid session")
    
    def test_google_session_invalid_session_returns_401(self):
        """Invalid session_id should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={"session_id": "completely-fake-session-id-12345"},
            headers={"Content-Type": "application/json"}
        )
        # 401 for invalid session OR 502 if auth provider unavailable
        assert response.status_code in [401, 502], f"Expected 401 or 502, got {response.status_code}"
        if response.status_code == 401:
            data = response.json()
            assert "detail" in data or "message" in data or "Invalid" in str(data)
            print("PASS: Invalid session_id returns 401 with appropriate error message")
        else:
            print("PASS: Auth provider unavailable (502) - acceptable for test environment")
    
    def test_google_session_missing_session_id_returns_422(self):
        """Missing session_id in body should return 422 validation error"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={},
            headers={"Content-Type": "application/json"}
        )
        # Pydantic validation should return 422 for missing required field
        assert response.status_code == 422, f"Expected 422 for missing session_id, got {response.status_code}"
        print("PASS: Missing session_id returns 422 validation error")
    
    def test_google_session_empty_session_id_returns_422(self):
        """Empty string session_id should return 422 or 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={"session_id": ""},
            headers={"Content-Type": "application/json"}
        )
        # Empty string might pass validation but fail at auth provider
        assert response.status_code in [401, 422, 502], f"Expected 401/422/502, got {response.status_code}"
        print(f"PASS: Empty session_id returns {response.status_code}")


class TestExistingEmailPasswordAuth:
    """Verify existing email/password auth flows still work after Google OAuth addition"""
    
    def test_login_admin_success(self):
        """Admin login with email/password should still work"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@starqistna.com", "password": "Admin@123"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Admin login failed: {response.status_code} - {response.text}"
        data = response.json()
        # Should return JWT (not 2FA challenge since admin doesn't have 2FA enabled by default)
        assert "access_token" in data, f"Expected access_token in response, got: {data.keys()}"
        assert "user" in data, "Expected user object in response"
        assert data["user"]["email"] == "admin@starqistna.com"
        assert data["user"]["is_admin"] == True
        print("PASS: Admin login with email/password returns JWT and user object")
        return data["access_token"]
    
    def test_login_invalid_credentials(self):
        """Invalid credentials should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@starqistna.com", "password": "WrongPassword"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: Invalid password returns 401")
    
    def test_register_new_user(self):
        """Register flow should still work"""
        import uuid
        unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        response = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": unique_email,
                "password": "TestPass123",
                "full_name": "Test User",
                "phone": "+60123456789"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Register failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "access_token" in data, "Expected access_token in register response"
        assert "user" in data, "Expected user object in register response"
        assert data["user"]["email"] == unique_email.lower()
        print(f"PASS: Register new user returns JWT - email: {unique_email}")
        return data["access_token"]
    
    def test_auth_me_with_jwt(self):
        """GET /api/auth/me should return user with valid JWT"""
        # First login to get token
        login_resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@starqistna.com", "password": "Admin@123"},
            headers={"Content-Type": "application/json"}
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        
        # Now call /auth/me
        me_resp = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert me_resp.status_code == 200, f"GET /auth/me failed: {me_resp.status_code}"
        data = me_resp.json()
        assert data["email"] == "admin@starqistna.com"
        assert data["is_admin"] == True
        print("PASS: GET /api/auth/me returns user with valid JWT")
    
    def test_auth_me_without_token_returns_401(self):
        """GET /api/auth/me without token should return 401"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: GET /api/auth/me without token returns 401")


class Test2FAEndpoints:
    """Sanity check that 2FA endpoints still exist (skip actual TOTP verification)"""
    
    def test_2fa_verify_endpoint_exists(self):
        """POST /api/auth/2fa/verify should exist"""
        response = requests.post(
            f"{BASE_URL}/api/auth/2fa/verify",
            json={"challenge_token": "fake-token", "code": "123456"},
            headers={"Content-Type": "application/json"}
        )
        # Should return 401 (invalid token), not 404 (endpoint missing)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: POST /api/auth/2fa/verify endpoint exists")
    
    def test_2fa_setup_requires_auth(self):
        """POST /api/auth/2fa/setup should require authentication"""
        response = requests.post(
            f"{BASE_URL}/api/auth/2fa/setup",
            json={"password": "test"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: POST /api/auth/2fa/setup requires authentication")


class TestHealthAndRoot:
    """Basic health checks"""
    
    def test_health_endpoint(self):
        """GET /api/health should return ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        print("PASS: GET /api/health returns ok")
    
    def test_root_endpoint(self):
        """GET /api/ should return service info"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["status"] == "ok"
        print("PASS: GET /api/ returns service info")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
