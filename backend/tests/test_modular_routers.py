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

pytest.importorskip("emergentintegrations")
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
        if getattr(r, "methods", None) and "POST" in r.methods
    }
    assert require_super_admin in deps["/api/admin/admins/invite"]
    assert require_admin in deps["/api/admin/promo-codes"]
