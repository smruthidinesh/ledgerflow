#!/usr/bin/env bash
# Render start command for the backend web service.
# Uses absolute paths so it works regardless of Render's working directory, and
# binds to Render's injected $PORT. Runs migrations + seed, then serves the API
# (with the outbox relay + event worker embedded via RUN_EMBEDDED_WORKERS).
set -e

cd /app/backend

# DB readiness + migrations + initial superuser
bash /app/backend/scripts/prestart.sh

# Serve on Render's port (defaults to 8000 locally). Use the venv binary explicitly.
exec /app/.venv/bin/fastapi run app/main.py --host 0.0.0.0 --port "${PORT:-8000}"
