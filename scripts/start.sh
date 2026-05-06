#!/bin/bash
# flatnotes-enhanced launcher.
# Pulls secrets from macOS keychain, activates venv, runs uvicorn.
# Invoked by ~/Library/LaunchAgents/com.xebyte.flatnotes.plist (wrapped in caffeinate -i).

set -euo pipefail

ROOT="/Users/ben/dev/flatnotes"
cd "$ROOT"

# Activate venv
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

# Pull secrets from keychain (no plaintext on disk)
FLATNOTES_PASSWORD=$(security find-generic-password -a ben -s flatnotes-password -w)
FLATNOTES_SECRET_KEY=$(security find-generic-password -a ben -s flatnotes-secret -w)

export FLATNOTES_PATH="/Users/ben/Notes/Notes"
export FLATNOTES_AUTH_TYPE="password"
export FLATNOTES_USERNAME="ben"
export FLATNOTES_PASSWORD
export FLATNOTES_SECRET_KEY

# 0.0.0.0 so both en0 (10.0.10.2) and en1 (10.0.10.118) can serve Traefik.
exec python -m uvicorn main:app \
    --app-dir server \
    --host 0.0.0.0 \
    --port 8088 \
    --proxy-headers \
    --forwarded-allow-ips '*'
