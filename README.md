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
- `APP_ENV=production` – startup fails without `STRIPE_WEBHOOK_SECRET` or explicit `CORS_ORIGINS`.
- `STRIPE_WEBHOOK_SECRET` – unsigned/invalid webhooks are rejected.
- `TRUST_PROXY_HEADERS=true` only behind a proxy that overwrites `X-Forwarded-For`.
- Gate-scanner accounts need role `gate_scanner` (admins also work).

## Health
- `GET /api/health` – liveness. `GET /api/ready` – MongoDB + seat-index readiness (503 if not).

## Tests
```
cd backend
python -m pytest tests/test_phase1_hardening.py tests/test_phase2_payments.py \
  tests/test_payment_finalization.py tests/test_stripe_webhook.py
MONGO_TEST_URL=mongodb://localhost:27017 python -m pytest tests/test_mongo_integration.py
```

## Deploy
`./scripts/deploy.sh` (requires an existing `backend/.venv`; it will not install into system Python).

## Recovery runbook
- **App refuses to start: seat index** – run `python backend/scripts/find_duplicate_seat_locks.py` (`--release-expired`), fix conflicts, restart.
- **Paid booking without ticket** – `POST /api/admin/bookings/{id}/gohub/retry`.
- **Transaction with `needs_refund_review`** – a second Stripe session was paid for an already-confirmed booking; refund it in the Stripe dashboard.
- **Stuck payment** – the reconciler re-checks Stripe every minute; check logs for `reconcile:`.
