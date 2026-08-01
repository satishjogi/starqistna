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
2. **Backend deps** — Activates your venv (auto-detects `.venv` or `venv`) and runs `pip install -r requirements.txt`.
3. **Frontend build** — Runs `yarn install --frozen-lockfile && yarn build`.
4. **Reload services** — Restarts `starqistna-backend` systemd unit and reloads nginx.

**First-run checklist:**
- The script will create your `.env` files from `.env.example` with placeholder values (`REPLACE_ME_*`).
- Edit `backend/.env` and set: `MONGO_URL`, `DB_NAME`, `STRIPE_API_KEY`, `JWT_SECRET`, `RESEND_API_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
- Edit `frontend/.env` and set: `REACT_APP_BACKEND_URL`, `REACT_APP_GOOGLE_CLIENT_ID`.
- Re-run `./scripts/deploy.sh`.
