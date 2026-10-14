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
#   2. Install/check Python dependencies and smoke-import the backend.
#   3. Build the React frontend.
#   4. Restart the configured systemd backend, verify health, and reload nginx.
#
# Configuration (override by exporting variables before running this script):
#   BACKEND_SERVICE       systemd unit name (default: starqistna-backend)
#   HEALTH_URL            readiness endpoint (default: http://127.0.0.1:8001/api/ready)
#   LIVE_URL              liveness endpoint (default: http://127.0.0.1:8001/api/health)
#   PIP_EXTRA_INDEX_URL   extra Python package index
#                         (default: https://d33sy5i8bnduwe.cloudfront.net/simple/)
#   HEALTH_TIMEOUT        health-check timeout in seconds (default: 60)
#
# Requires: bash, python3 (with venv already created), npm or yarn, systemctl, curl.
# ---------------------------------------------------------------------------
set -euo pipefail

BACKEND_SERVICE="${BACKEND_SERVICE:-starqistna-backend}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/api/ready}"
LIVE_URL="${LIVE_URL:-http://127.0.0.1:8001/api/health}"
PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://d33sy5i8bnduwe.cloudfront.net/simple/}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"

if ! [[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] || (( 10#$HEALTH_TIMEOUT < 1 )); then
    echo "ERROR: HEALTH_TIMEOUT must be a positive integer number of seconds." >&2
    exit 1
fi
HEALTH_TIMEOUT_SECONDS=$((10#$HEALTH_TIMEOUT))

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf "\n\033[1;34m▸ %s\033[0m\n" "$*"; }
warn() { printf "\n\033[1;33m! %s\033[0m\n" "$*"; }

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required for backend health checks. Install curl and retry." >&2
    exit 1
fi

wait_for_health() {
    local url="$1"
    local label="$2"
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
    local remaining
    local request_timeout

    while :; do
        remaining=$((deadline - SECONDS))
        if (( remaining < 1 )); then
            echo "ERROR: Backend $label check did not return HTTP 200 at $url within ${HEALTH_TIMEOUT_SECONDS}s." >&2
            return 1
        fi
        request_timeout=$remaining
        if (( request_timeout > 2 )); then
            request_timeout=2
        fi
        if curl -fsS --max-time "$request_timeout" "$url" >/dev/null 2>&1; then
            echo "  ✓ backend $label check passed: $url"
            return 0
        fi
        remaining=$((deadline - SECONDS))
        if (( remaining < 1 )); then
            echo "ERROR: Backend $label check did not return HTTP 200 at $url within ${HEALTH_TIMEOUT_SECONDS}s." >&2
            return 1
        fi
        if (( remaining > 2 )); then
            sleep 2
        else
            sleep "$remaining"
        fi
    done
}

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
    echo "ERROR: no virtualenv at backend/.venv or backend/venv. Refusing to pip install into the system Python." >&2
    echo "       Create one first: python3 -m venv backend/.venv" >&2
    exit 1
fi
python -m pip install -r "$REPO_ROOT/backend/requirements.txt" --extra-index-url "$PIP_EXTRA_INDEX_URL"
python -m pip check
(cd "$REPO_ROOT/backend" && python -c "import server")
echo "  ✓ backend deps up to date"

log "3/4  Building frontend"
pushd "$REPO_ROOT/frontend" >/dev/null
# npm ci installs EXACTLY what package-lock.json pins (reproducible builds).
# --legacy-peer-deps is required because react-scripts@5 declares eslint@8-era
# peers while the repo also carries newer plugins — the lockfile already
# encodes a consistent tree.
if [[ -f package-lock.json ]]; then
    npm ci --legacy-peer-deps
elif command -v yarn >/dev/null 2>&1 && [[ -f yarn.lock ]]; then
    yarn install --frozen-lockfile
else
    npm install --legacy-peer-deps
fi
npm run build
popd >/dev/null

log "4/4  Restarting backend and checking health"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemctl is required to restart backend unit '$BACKEND_SERVICE'." >&2
    exit 1
fi
if ! sudo systemctl cat "$BACKEND_SERVICE" >/dev/null; then
    echo "ERROR: systemd unit '$BACKEND_SERVICE' was not found or could not be read. Set BACKEND_SERVICE to the correct unit name." >&2
    exit 1
fi
if ! sudo systemctl restart "$BACKEND_SERVICE"; then
    echo "ERROR: Failed to restart systemd unit '$BACKEND_SERVICE'. Recent service details:" >&2
    sudo systemctl status --no-pager "$BACKEND_SERVICE" >&2 || true
    sudo journalctl -u "$BACKEND_SERVICE" -n 50 --no-pager >&2 || true
    exit 1
fi
echo "  ✓ backend restarted via systemd: $BACKEND_SERVICE"

if ! sudo systemctl is-active --quiet "$BACKEND_SERVICE"; then
    echo "ERROR: systemd unit '$BACKEND_SERVICE' is not active after restart. Recent service details:" >&2
    sudo systemctl status --no-pager "$BACKEND_SERVICE" >&2 || true
    sudo journalctl -u "$BACKEND_SERVICE" -n 50 --no-pager >&2 || true
    exit 1
fi

wait_for_health "$LIVE_URL" "liveness"
wait_for_health "$HEALTH_URL" "readiness"

if systemctl list-unit-files --type=service --no-legend | awk '$1 == "nginx.service" { found=1 } END { exit !found }'; then
    sudo systemctl reload nginx
    echo "  ✓ nginx reloaded"
elif command -v nginx >/dev/null 2>&1; then
    sudo nginx -s reload
    echo "  ✓ nginx reloaded (via nginx -s reload)"
else
    warn "nginx not found — skipping. Serve /app/frontend/build/ with your web server of choice."
fi

log "Deploy complete."
echo ""
echo "If any keys were added as placeholders (see step 1), edit them now:"
echo "  nano $REPO_ROOT/backend/.env"
echo "  nano $REPO_ROOT/frontend/.env  # then re-run: ./scripts/deploy.sh"
