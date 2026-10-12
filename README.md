# Starqistna – Transit E1 Bus Booking

FastAPI + MongoDB backend (`backend/`) and Create React App frontend (`frontend/`, built with `craco`).
Deployment is described in `scripts/README.md` and `scripts/deploy.sh`.

## Backend layout

`backend/server.py` is the application entrypoint (`server:app`). It builds the `FastAPI` app, configures CORS,
and runs startup/shutdown tasks (seeding, migrations, index creation, payment reconciliation). Domain endpoints
live in `backend/routers/`.

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
| `routers/auth.py` | `/api/auth/*` (registration, login, 2FA, recovery, profile, Google OAuth) |
| `routers/search.py` | `GET /api/search`, `/api/schedules/{schedule_id}`, `/api/popular/now` |
| `routers/seats.py` | `POST /api/seats/lock`, `/api/seats/release` |
| `routers/bookings.py` | `/api/bookings*`, boarding validation, admin booking statistics/list |
| `routers/payments.py` | `/api/payments*`, Stripe webhook, payment reconciliation and admin payment/audit views |
| `routers/routes.py` | `/api/admin/routes*` |
| `routers/schedules.py` | `/api/admin/schedules*` |
| `routers/bus_types.py` | `/api/bus-types`, `/api/admin/bus-types*` |
| `routers/gohub.py` | Admin GoHub retry and probe endpoints |
| `routers/settings.py` | `GET /api/settings`, `PATCH /api/admin/settings` |

Shared multi-router lookups and ticket issuance live in `backend/services/`. Startup seed, migration, and index tasks remain in `server.py`.

### How routes are assembled

Each router module defines `router = APIRouter()` with **no prefix**. To add a router, define its endpoints in
`backend/routers/<domain>.py`, import that module in `server.py`, then call
`api.include_router(<domain>_router.router)`. `server.py` creates `api = APIRouter(prefix="/api")` and finally calls
`app.include_router(api)`, preserving all `/api/...` paths. Shared dependencies come from `core/` and shared
business logic from `services/`; routers must not import `server.py`.

### Running and testing

Copy `backend/.env.example` to `backend/.env` and fill it in (the app refuses to start without the required vars), then:

```
pip install -r backend/requirements.txt
cd backend && python -m pytest tests/test_modular_routers.py
```

Other tests under `backend/tests/` (and `tests/`) exercise a running API/DB. Frontend scripts (`start`, `build`, `test`) are defined in `frontend/package.json`; see `scripts/README.md` for how the app is served in deployment.

## Migration note

The extraction-only phases preserve existing API and business behavior. The next phase, after modularization
stabilizes, is the separate route/schedule UX redesign; it is not part of this backend extraction.
