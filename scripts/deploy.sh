#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Star Qistna — VPS deployment helper.
# Safe to re-run: idempotent. Add any missing env keys from .env.example
# without ever overwriting values you have already set.
#
# Usage on the VPS after a `git pull`:
#     cd ~/app && ./scripts/deploy.sh
#
# What it does:
#   1. For backend/.env  and frontend/.env, add any keys defined in the
#      matching .env.example that are missing locally (values left as
#      placeholders — you fill them in on first run only).
#   2. Refresh Python dependencies (`pip install -r requirements.txt`).
#   3. Build the React frontend (`yarn install --frozen-lockfile && yarn build`).
#   4. Reload systemd + nginx so the new build is served.
#
# Requires: bash, python3 (with venv already created), yarn, systemctl.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf "\n\033[1;34m▸ %s\033[0m\n" "$*"; }
warn() { printf "\n\033[1;33m! %s\033[0m\n" "$*"; }

sync_env() {
    local example="$1"
    local target="$2"
    if [[ ! -f "$example" ]]; then
        warn "Missing $example — skipping sync."
        return 0
    fi
    if [[ ! -f "$target" ]]; then
        log "Creating $target from $example (placeholders — fill in secrets!)"
        cp "$example" "$target"
        return 0
    fi
    local added=0
    while IFS= read -r line; do
        # Skip blanks and comments
        [[ -z "${line// }" || "$line" =~ ^# ]] && continue
        local key="${line%%=*}"
        # If the key does NOT already exist in target, append the example line.
        if ! grep -qE "^${key}=" "$target"; then
            echo "" >> "$target"
            echo "$line" >> "$target"
            echo "  + added ${key} (placeholder value — please fill in)"
            added=$((added + 1))
        fi
    done < "$example"
    if [[ $added -eq 0 ]]; then
        echo "  ✓ $target already has all keys from example"
    fi
}

log "1/4  Syncing env files"
sync_env "$REPO_ROOT/backend/.env.example"  "$REPO_ROOT/backend/.env"
sync_env "$REPO_ROOT/frontend/.env.example" "$REPO_ROOT/frontend/.env"

log "2/4  Installing backend dependencies"
if [[ -d "$REPO_ROOT/backend/.venv" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/backend/.venv/bin/activate"
elif [[ -d "$REPO_ROOT/backend/venv" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/backend/venv/bin/activate"
else
    warn "No venv found at backend/.venv or backend/venv. Skipping pip install."
fi
if command -v pip >/dev/null 2>&1; then
    pip install -q -r "$REPO_ROOT/backend/requirements.txt"
    echo "  ✓ backend deps up to date"
fi

log "3/4  Building frontend"
pushd "$REPO_ROOT/frontend" >/dev/null
yarn install --frozen-lockfile
yarn build
popd >/dev/null

log "4/4  Restarting services"

# Try every common supervisor the operator might have used, in order:
#   1. systemd unit (any name containing qistna / starqistna / bus / backend)
#   2. PM2 process
#   3. supervisord managed process
#   4. Nothing matched — surface actionable instructions.
BACKEND_RESTARTED=0

if command -v systemctl >/dev/null 2>&1; then
    # Enumerate ALL loaded units (running, enabled, or otherwise) — much broader
    # than list-unit-files, which misses transient / manually-created units.
    CANDIDATE_UNITS=$(systemctl list-units --type=service --all --no-legend 2>/dev/null \
        | awk '{print $1}' \
        | grep -iE '(qistna|starqistna|star-qistna|star_qistna|fastapi|uvicorn|gunicorn|bus-backend|bus_backend)' \
        | head -3 || true)
    for unit in $CANDIDATE_UNITS; do
        if sudo systemctl restart "$unit" 2>/dev/null; then
            echo "  ✓ backend restarted via systemd: $unit"
            BACKEND_RESTARTED=1
            break
        fi
    done
fi

if [[ $BACKEND_RESTARTED -eq 0 ]] && command -v pm2 >/dev/null 2>&1; then
    PM2_TARGET=$(pm2 list 2>/dev/null | awk '/qistna|starqistna|bus|backend|fastapi|uvicorn/ && !/^─/ {print $2; exit}' || true)
    if [[ -n "$PM2_TARGET" ]]; then
        pm2 restart "$PM2_TARGET" >/dev/null && \
            echo "  ✓ backend restarted via pm2: $PM2_TARGET" && BACKEND_RESTARTED=1
    fi
fi

if [[ $BACKEND_RESTARTED -eq 0 ]] && command -v supervisorctl >/dev/null 2>&1; then
    SUP_TARGET=$(sudo supervisorctl status 2>/dev/null \
        | awk '/qistna|starqistna|backend|bus/ {print $1; exit}' || true)
    if [[ -n "$SUP_TARGET" ]]; then
        sudo supervisorctl restart "$SUP_TARGET" >/dev/null && \
            echo "  ✓ backend restarted via supervisord: $SUP_TARGET" && BACKEND_RESTARTED=1
    fi
fi

if [[ $BACKEND_RESTARTED -eq 0 ]]; then
    warn "Could not auto-detect backend supervisor. Restart manually with ONE of:"
    echo "     sudo systemctl restart <your-service-name>     # if using systemd"
    echo "     pm2 restart <your-app-name>                    # if using PM2"
    echo "     sudo supervisorctl restart <your-program>      # if using supervisord"
    echo ""
    echo "  To see what's running:"
    echo "     sudo systemctl list-units --type=service | grep -iE 'star|qistna|fastapi|uvicorn|gunicorn'"
    echo "     pm2 list"
    echo "     sudo supervisorctl status"
fi

if systemctl list-unit-files 2>/dev/null | grep -qE "^nginx(\.service)?\s"; then
    sudo systemctl reload nginx
    echo "  ✓ nginx reloaded"
elif command -v nginx >/dev/null 2>&1; then
    sudo nginx -s reload 2>/dev/null && echo "  ✓ nginx reloaded (via nginx -s reload)" \
        || warn "nginx found but reload failed — reload it manually."
else
    warn "nginx not found — skipping. Serve /app/frontend/build/ with your web server of choice."
fi

log "Deploy complete."
echo ""
echo "If any keys were added as placeholders (see step 1), edit them now:"
echo "  nano $REPO_ROOT/backend/.env"
echo "  nano $REPO_ROOT/frontend/.env  # then re-run: ./scripts/deploy.sh"
