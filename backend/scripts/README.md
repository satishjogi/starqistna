# Backend Operational Scripts

Standalone CLI tools for production/staging maintenance. Each script is safe
to run against a live database and reads its config from `backend/.env`.

## Available scripts

### `reset_admin_password.py`
Reset the bootstrap super-admin password on the VPS when the admin account
is locked out.

```bash
cd ~/app/backend
source .venv/bin/activate   # or venv/bin/activate depending on your setup

# Interactive (safest — prompts for password, hidden input)
python scripts/reset_admin_password.py

# Non-interactive
python scripts/reset_admin_password.py --password 'MyStrong!Pass123'

# Use the value already in BOOTSTRAP_ADMIN_PASSWORD env var
python scripts/reset_admin_password.py --from-env
```

### Alternative: env-flag recovery (no shell access to Python needed)
If you can only edit `.env` and restart the backend, add this line temporarily:

```
RESET_ADMIN_ON_BOOT=true
```

Restart the backend (`sudo systemctl restart starqistna-backend`). The seed
routine will re-sync the admin password to the current
`BOOTSTRAP_ADMIN_PASSWORD` value. **Then remove the flag** and restart again
so it doesn't run every boot.
