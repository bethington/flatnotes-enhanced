#!/bin/bash
# flatnotes-enhanced launcher.
# Pulls secrets from macOS keychain, activates venv, runs uvicorn.
# Invoked by ~/Library/LaunchAgents/com.xebyte.flatnotes.plist (wrapped in caffeinate -i).

set -euo pipefail

ROOT="/Users/ben/dev/flatnotes"
cd "$ROOT"

# LaunchAgent inherits a minimal PATH that excludes /opt/homebrew/bin and
# /usr/local/bin, where most user-installed CLIs live (claude, ffmpeg, docker,
# git, etc.). Subprocesses spawned from this server (voiceprints.py exec'ing
# `docker`, ffmpeg concat, claude CLI) need these on PATH. Setting it BEFORE
# the venv activation matters: the venv prepends its own bin/, so the final
# order is .venv/bin -> /opt/homebrew/bin -> /usr/local/bin -> default. If
# this export came AFTER `source .venv/bin/activate`, it would clobber the
# venv's prepend and `python` would resolve to homebrew (no uvicorn there).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Activate venv
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

# Make personal-agent-mcp importable so the meeting upload route can call
# meetings_mcp.pipeline directly (no extra subprocess hop for a deterministic
# operation). Co-developed sibling repo; coupling here is intentional.
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/Users/ben/dev/personal-agent-mcp"

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
