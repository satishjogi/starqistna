# Star Qistna (Qistna Express)

Bus booking platform: FastAPI + MongoDB backend (`backend/server.py`), React frontend (`frontend/`).

## Local setup
```
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in secrets
uvicorn server:app --reload
cd ../frontend && yarn install && yarn start
```

## Required production configuration
- Set `APP_ENV=production` explicitly. Startup fails if the environment is missing or unknown.
- Production startup rejects missing/placeholder `STRIPE_WEBHOOK_SECRET`, `BOARDING_API_KEY`, `JWT_SECRET`, or `BOOTSTRAP_ADMIN_PASSWORD`; `STRIPE_API_KEY` must be a live key (`sk_live_...`).
- Production startup also requires explicit non-wildcard `CORS_ORIGINS` and refuses `SEED_DEMO_DATA=true`. The deployment script applies the same checks before restarting the service.
- `STRIPE_WEBHOOK_SECRET` – unsigned/invalid webhooks are rejected, and production startup fails closed if the secret is not configured.
- `TRUST_PROXY_HEADERS=true` only behind a proxy that overwrites `X-Forwarded-For`.
- `CHECKOUT_ALLOWED_ORIGINS` – comma-separated trusted origins (e.g. `https://starqistna.com,https://www.starqistna.com`) for Stripe success/cancel return URLs. Falls back to the non-wildcard `CORS_ORIGINS`. The first entry is the default.
- Gate-scanner accounts need role `gate_scanner` (admins also work).

## Health
- `GET /api/health` – liveness. `GET /api/ready` – MongoDB + leg-level seat-index (`seat_leg_locks`) readiness (503 if not).

## Tests
```
cd backend
python -m pytest tests/test_phase1_hardening.py tests/test_phase2_payments.py \
  tests/test_payment_finalization.py tests/test_stripe_webhook.py
MONGO_TEST_URL=mongodb://localhost:27017 python -m pytest tests/test_mongo_integration.py
MONGO_TEST_URL=mongodb://localhost:27017 python -m pytest tests/test_bus_booking_api.py
python -m pytest tests/test_startup_security.py
```
The booking API integration tests create and drop uniquely named `starqistna_booking_test_*` databases; set `MONGO_TEST_URL` to a dedicated test MongoDB, never a production instance. Stripe checkout is stubbed, so no Stripe account or live key is needed. CI runs these tests against its MongoDB service.

## Deploy
`./scripts/deploy.sh` (requires an existing `backend/.venv`; it will not install into system Python). It restarts `starqistna-backend` and checks backend liveness/readiness before reporting success; deployment settings can be overridden with environment variables documented in [`scripts/README.md`](scripts/README.md).

## Seat-lock release
`POST /api/seats/release` only deletes `locked` seat locks that are not attached to a booking. Previously it could delete locks already claimed by a booking awaiting payment (`pending_payment`), letting another customer take the seat while the first payment was processing. Attached locks now stay until payment resolves or the hold expires (the sweeper / booking cancellation frees them).

## Checkout security
`POST /api/payments/checkout`:
- **Authorization** – caller must be the booking owner, an admin, or a guest presenting the booking's `lock_token` (from `/seats/lock`) or its `contact_email` (`email`). Anyone else gets `404 Booking not found`, identical to a missing booking, so IDs can't be probed.
- **Payment state** – rejected unless the booking is `pending_payment` with `payment_status=pending` (`400` if already paid, `409` if cancelled/expired/refunded/confirmed) and every seat lock is still held and unexpired (`409`).
- **Return URLs** – `origin_url` must exactly match an origin in `CHECKOUT_ALLOWED_ORIGINS` (http/https only; userinfo, paths, queries, protocol-relative and look-alike hosts are refused). Untrusted values fall back to the first trusted origin (`400` if none is configured), so the receipt token is only ever sent to a trusted domain.

## Recovery runbook
- **App refuses to start: seat index** – run the read-only `python backend/scripts/check_segment_seat_migration.py`, fix reported conflicts, restart. (`find_duplicate_seat_locks.py` is legacy and refuses to run once `seat_leg_locks` exists.)
- **Paid booking without ticket** – `POST /api/admin/bookings/{id}/gohub/retry`.
- **Transaction with `needs_refund_review`** – a second Stripe session was paid for an already-confirmed booking; refund it in the Stripe dashboard.
- **Stuck payment** – the reconciler re-checks Stripe every minute; check logs for `reconcile:`.
