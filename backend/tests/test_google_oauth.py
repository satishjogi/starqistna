"""Tests for Google OAuth authorization-code login and existing auth flows."""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8001').rstrip('/')

class TestGoogleOAuthEndpoint:
    """Tests for POST /api/auth/google/callback endpoint"""
    
    def test_google_callback_endpoint_exists(self):
        """Missing authorization code is rejected by request validation."""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/callback",
            json={"redirect_uri": "http://localhost:3000/auth/callback"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422, response.text
    
    def test_google_callback_invalid_code_is_rejected(self):
        response = requests.post(
            f"{BASE_URL}/api/auth/google/callback",
            json={"code": "invalid-test-code", "redirect_uri": "http://localhost:3000/auth/callback"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [401, 500, 502], response.text
    
    def test_google_callback_missing_redirect_uri_returns_422(self):
        response = requests.post(
            f"{BASE_URL}/api/auth/google/callback",
            json={"code": "test-code"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422, response.text


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
        assert data["user"]["is_admin"]
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
        assert data["is_admin"]
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
