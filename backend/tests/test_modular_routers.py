"""Guards the phase-1 backend modularization: extracted endpoints stay reachable
under their original /api paths, and each is registered exactly once."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for k, v in {
    "MONGO_URL": "mongodb://localhost:27017",
    "DB_NAME": "test_db",
    "JWT_SECRET": "test-secret",
    "STRIPE_API_KEY": "sk_test_dummy",
    "BOOTSTRAP_ADMIN_PASSWORD": "Test-Password-1",
}.items():
    os.environ.setdefault(k, v)

import server  # noqa: E402

EXPECTED = {
    ("GET", "/api/terminals"),
    ("GET", "/api/admin/terminals"),
    ("POST", "/api/admin/terminals"),
    ("PATCH", "/api/admin/terminals/{terminal_id}"),
    ("DELETE", "/api/admin/terminals/{terminal_id}"),
    ("POST", "/api/promo/validate"),
    ("GET", "/api/admin/promo-codes"),
    ("POST", "/api/admin/promo-codes"),
    ("PATCH", "/api/admin/promo-codes/{code_id}"),
    ("DELETE", "/api/admin/promo-codes/{code_id}"),
    ("POST", "/api/feedback"),
    ("GET", "/api/admin/feedback"),
    ("PATCH", "/api/admin/feedback/{feedback_id}"),
    ("GET", "/api/admin/admins"),
    ("POST", "/api/admin/admins/invite"),
    ("POST", "/api/admin/admins/accept-invite"),
    ("PATCH", "/api/admin/admins/{admin_id}/role"),
    ("PATCH", "/api/admin/admins/{admin_id}/active"),
    ("DELETE", "/api/admin/admins/{admin_id}"),
    ("DELETE", "/api/admin/admins/invites/{invite_id}"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/2fa/verify"),
    ("POST", "/api/auth/forgot-password"),
    ("POST", "/api/auth/reset-password"),
    ("POST", "/api/auth/2fa/setup"),
    ("POST", "/api/auth/2fa/enable"),
    ("POST", "/api/auth/2fa/disable"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/set-password"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/google/callback"),
    ("GET", "/api/search"),
    ("GET", "/api/popular/now"),
    ("GET", "/api/schedules/{schedule_id}"),
    ("POST", "/api/seats/lock"),
    ("POST", "/api/seats/release"),
    ("POST", "/api/boarding/validate"),
    ("POST", "/api/bookings"),
    ("GET", "/api/bookings/me"),
    ("GET", "/api/bookings/{booking_id}"),
    ("GET", "/api/bookings/{booking_id}/cancellation-quote"),
    ("POST", "/api/bookings/{booking_id}/cancel"),
    ("GET", "/api/admin/stats"),
    ("GET", "/api/admin/bookings"),
    ("GET", "/api/payments/options/{booking_id}"),
    ("POST", "/api/payments/checkout"),
    ("GET", "/api/payments/status/{session_id}"),
    ("POST", "/api/admin/payments/reconcile"),
    ("POST", "/api/admin/payments/reconcile/{session_id}"),
    ("POST", "/api/webhook/stripe"),
    ("GET", "/api/admin/payments"),
    ("GET", "/api/admin/audit-logs"),
    ("GET", "/api/admin/routes"),
    ("GET", "/api/admin/routes/{route_id}"),
    ("POST", "/api/admin/routes"),
    ("PATCH", "/api/admin/routes/{route_id}"),
    ("DELETE", "/api/admin/routes/{route_id}"),
    ("GET", "/api/admin/schedules"),
    ("POST", "/api/admin/schedules"),
    ("POST", "/api/admin/schedules/bulk"),
    ("DELETE", "/api/admin/schedules/range"),
    ("GET", "/api/admin/schedules/{schedule_id}/impact"),
    ("PATCH", "/api/admin/schedules/{schedule_id}"),
    ("POST", "/api/admin/schedules/bulk-delete/preview"),
    ("POST", "/api/admin/schedules/bulk-delete"),
    ("GET", "/api/bus-types"),
    ("GET", "/api/admin/bus-types"),
    ("POST", "/api/admin/bus-types"),
    ("PATCH", "/api/admin/bus-types/{bus_type_id}"),
    ("DELETE", "/api/admin/bus-types/{bus_type_id}"),
    ("POST", "/api/admin/bookings/{booking_id}/gohub/retry"),
    ("POST", "/api/admin/gohub/probe"),
    ("GET", "/api/settings"),
    ("PATCH", "/api/admin/settings"),
}


def _routes():
    out = []
    for r in server.app.routes:
        for m in getattr(r, "methods", None) or []:
            out.append((m, r.path))
    return out


def test_extracted_routes_present_exactly_once():
    routes = _routes()
    for key in EXPECTED:
        assert routes.count(key) == 1, key


def test_admin_dependencies_unchanged():
    from core.security import require_admin, require_super_admin
    deps = {
        r.path: {d.call for d in r.dependant.dependencies}
        for r in server.app.routes
        if getattr(r, "methods", None) and hasattr(r, "dependant")
    }
    assert require_super_admin in deps["/api/admin/admins/invite"]
    assert require_admin in deps["/api/admin/promo-codes"]
    assert require_admin in deps["/api/admin/routes"]
    assert require_admin in deps["/api/admin/schedules"]
    assert require_admin in deps["/api/admin/bus-types"]
    assert require_admin in deps["/api/admin/settings"]
