#!/usr/bin/env bash
# start.sh — Inicia o dashboard yt-pub-lives
set -euo pipefail

PORT="${1:-8090}"
DIR="$(cd "$(dirname "$0")" && pwd)"

export PATH="/usr/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/google-cloud-sdk/bin:$PATH"

# Mata instancia anterior se existir
fuser -k "$PORT/tcp" 2>/dev/null || true
sleep 1

echo "==> Dashboard: http://localhost:$PORT"
cd "$DIR/dashboard" && python3 server.py "$PORT"
