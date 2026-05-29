#!/bin/sh
set -e

APP_PORT="${PORT:-8000}"
WEB_WORKERS="${WEB_CONCURRENCY:-1}"

# Run migrations. If Alembic can't find the current revision (e.g. after
# migration consolidation where old revision IDs were removed), stamp the
# database to the new consolidated head and retry.
echo "Running database migrations..."
if ! alembic upgrade head 2>&1; then
    echo "Migration failed - stamping database to consolidated migration head and retrying..."
    alembic stamp --purge 0001_initial_schema
    alembic upgrade head
fi

echo "Starting API on 0.0.0.0:${APP_PORT} with ${WEB_WORKERS} worker(s)..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}" --workers "${WEB_WORKERS}"
