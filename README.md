# Starqistna – Transit E1 Bus Booking

FastAPI + MongoDB backend (`backend/`) and Create React App frontend (`frontend/`, built with `craco`).
Deployment is described in `scripts/README.md` and `scripts/deploy.sh`.

## Backend layout

`backend/server.py` is the application entrypoint (`server:app`). It builds the `FastAPI` app, configures CORS,
runs startup/shutdown tasks (seeding, migrations, index creation, payment reconciliation) and still contains
the not-yet-extracted domains.

| Module | Responsibility |
| --- | --- |
| `core/config.py` | Env vars (`MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `STRIPE_API_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, …), Mongo `client`/`db`, `logger` |
| `core/utils.py` | `utcnow()`, `new_id()` |
| `core/security.py` | Password hashing, JWT, `current_user` / `require_user` / `require_admin` / `require_super_admin`, auth throttling, password strength |
| `core/audit.py` | `log_audit()` helper |
| `core/pricing.py` | Pure `_calc_pricing()` used by bookings and promo validation |
| `routers/terminals.py` | `GET /api/terminals`, `/api/admin/terminals*` |
| `routers/promo.py` | `POST /api/promo/validate`, `/api/admin/promo-codes*` |
| `routers/feedback.py` | `POST /api/feedback`, `/api/admin/feedback*` |
| `routers/admin_users.py` | `/api/admin/admins*` (invite, accept-invite, role, active, revoke) |

Still in `server.py` (to be extracted later): search, seats, bookings, Stripe/payments/reconciliation,
GoHub/CTS, routes, schedules, bus types, app settings, auth endpoints, seeds/migrations/indexes.

### How routes are assembled

Each router module defines `router = APIRouter()` with **no prefix**. `server.py` creates
`api = APIRouter(prefix="/api")`, registers its own endpoints on it, calls `api.include_router(<module>.router)` for
each extracted module, and finally `app.include_router(api)`. Every endpoint therefore keeps its `/api/...` path.
Shared dependencies live in `core/` and are imported by both `server.py` and the routers, so routers never import
`server.py` (no circular imports).

### Running and testing

Copy `backend/.env.example` to `backend/.env` and fill it in (the app refuses to start without the required vars), then:

```
pip install -r backend/requirements.txt
cd backend && python -m pytest tests/test_modular_routers.py
```

Other tests under `backend/tests/` (and `tests/`) exercise a running API/DB. Frontend scripts (`start`, `build`, `test`) are defined in `frontend/package.json`; see `scripts/README.md` for how the app is served in deployment.

## Migration note

This is phase 1 of backend modularization (no API or behavior changes). Later PRs will extract
booking, search, payment, routes and schedules into routers once this foundation is merged.
