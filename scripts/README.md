# Deploy Scripts

## `deploy.sh`

One-shot idempotent deploy for the Hostinger VPS. Run this every time you `git pull` a new version.

```bash
cd ~/app
git pull
./scripts/deploy.sh
```

**What it does (safe to re-run):**

1. **Env sync** — For both `backend/.env` and `frontend/.env`, adds any keys that appear in the matching `.env.example` but are missing locally. Existing values are **never overwritten**. New keys are added with placeholder values which you fill in once.
2. **Backend deps** — Activates your venv (auto-detects `.venv` or `venv`), installs `requirements.txt` with the configured extra package index, runs `pip check`, and smoke-imports `server`.
3. **Frontend build** — Installs frontend dependencies and builds the React app.
4. **Restart and verify** — Restarts the configured systemd unit, verifies it is active, and polls both backend health endpoints. Then reloads nginx when installed.

**Configuration:** Export these variables before running the script to override their defaults:

| Variable | Default |
|---|---|
| `BACKEND_SERVICE` | `starqistna-backend` |
| `HEALTH_URL` | `http://127.0.0.1:8001/api/ready` |
| `LIVE_URL` | `http://127.0.0.1:8001/api/health` |
| `PIP_EXTRA_INDEX_URL` | `https://d33sy5i8bnduwe.cloudfront.net/simple/` |
| `HEALTH_TIMEOUT` | `60` seconds |

For example: `HEALTH_TIMEOUT=90 ./scripts/deploy.sh`. The script fails if the service cannot be restarted or either health endpoint does not return HTTP 200.

**First-run checklist:**
- The script will create your `.env` files from `.env.example` with placeholder values (`REPLACE_ME_*`).
- Edit `backend/.env` and set: `MONGO_URL`, `DB_NAME`, `STRIPE_API_KEY`, `JWT_SECRET`, `RESEND_API_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
- Edit `frontend/.env` and set: `REACT_APP_BACKEND_URL`, `REACT_APP_GOOGLE_CLIENT_ID`.
- Re-run `./scripts/deploy.sh`.
