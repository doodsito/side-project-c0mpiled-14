#!/usr/bin/env bash
# Restarts the dev server on PORT (default 8000).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
pkill -f "uvicorn server:app" 2>/dev/null || true
sleep 1
exec uv run uvicorn server:app --port "$PORT"
