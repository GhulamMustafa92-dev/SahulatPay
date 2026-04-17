#!/usr/bin/env bash
# Railway start command — runs Alembic migrations, then boots the API.
set -e

echo "[start] Running database migrations..."
alembic upgrade head

echo "[start] Launching uvicorn on 0.0.0.0:${PORT:-8000} ..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
