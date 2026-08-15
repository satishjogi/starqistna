"""Bus Types CRUD tests — validates seed, admin CRUD, in-use guard, dupe guard, and public read.
"""
import os
import uuid
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def _admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@starqistna.com", "password": "Admin@123"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def test_public_bus_types_seeded():
    """Backend seeds 3 default bus types (VIP 27, Executive, Standard) on startup."""
    r = requests.get(f"{BASE_URL}/api/bus-types", timeout=10)
    assert r.status_code == 200, r.text
    names = {bt["name"] for bt in r.json()}
    assert {"VIP 27", "Executive", "Standard"}.issubset(names)


def test_admin_list_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/bus-types", timeout=10)
    assert r.status_code == 401


def test_bus_type_full_crud_cycle():
    token = _admin_token()
    h = {"Authorization": f"Bearer {token}"}
    unique_name = f"Test Sleeper {uuid.uuid4().hex[:6]}"

    # CREATE
    r = requests.post(
        f"{BASE_URL}/api/admin/bus-types",
        json={"name": unique_name, "seat_count": 30, "description": "Test bed bus"},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    created = r.json()
    bt_id = created["id"]
    assert created["seat_count"] == 30
    assert created["layout"] == "2+2"

    # LIST includes it
    r = requests.get(f"{BASE_URL}/api/admin/bus-types", headers=h, timeout=10)
    assert any(bt["id"] == bt_id for bt in r.json())

    # DUPLICATE name → 400
    r = requests.post(
        f"{BASE_URL}/api/admin/bus-types",
        json={"name": unique_name, "seat_count": 40},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"].lower()

    # UPDATE seat_count
    r = requests.patch(
        f"{BASE_URL}/api/admin/bus-types/{bt_id}",
        json={"seat_count": 22, "description": "Updated desc"},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["seat_count"] == 22

    # RENAME to name starting with VIP → layout flips to 2+1
    r = requests.patch(
        f"{BASE_URL}/api/admin/bus-types/{bt_id}",
        json={"name": f"VIP {unique_name}"},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["layout"] == "2+1"

    # DELETE succeeds (unused)
    r = requests.delete(f"{BASE_URL}/api/admin/bus-types/{bt_id}", headers=h, timeout=10)
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # DELETE again → 404
    r = requests.delete(f"{BASE_URL}/api/admin/bus-types/{bt_id}", headers=h, timeout=10)
    assert r.status_code == 404


def test_delete_in_use_bus_type_blocked():
    """Standard is referenced by seeded schedules — delete must be blocked."""
    token = _admin_token()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BASE_URL}/api/admin/bus-types", headers=h, timeout=10)
    std = next((bt for bt in r.json() if bt["name"] == "Standard"), None)
    assert std is not None
    r = requests.delete(f"{BASE_URL}/api/admin/bus-types/{std['id']}", headers=h, timeout=10)
    assert r.status_code == 400
    assert "cannot delete" in r.json()["detail"].lower()


def test_seat_count_bounds_validation():
    token = _admin_token()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{BASE_URL}/api/admin/bus-types",
        json={"name": "Too Small", "seat_count": 5},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 422  # Pydantic validation error
    r = requests.post(
        f"{BASE_URL}/api/admin/bus-types",
        json={"name": "Too Big", "seat_count": 100},
        headers=h,
        timeout=10,
    )
    assert r.status_code == 422
