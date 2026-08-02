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
if systemctl list-unit-files | grep -q starqistna-backend; then
    sudo systemctl restart starqistna-backend
    echo "  ✓ backend restarted"
else
    warn "starqistna-backend systemd unit not found — skipping backend restart."
fi
if systemctl list-unit-files | grep -qE "^nginx(\.service)?\s"; then
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
