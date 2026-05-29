#!/bin/sh
set -e

APP_PORT="${PORT:-8000}"
WEB_WORKERS="${WEB_CONCURRENCY:-1}"

echo "Teamy entrypoint reached; running Docker image startup command."

# Run migrations. If Alembic can't find the current revision (e.g. after
# migration consolidation where old revision IDs were removed), stamp the
# database to the new consolidated head and retry. Other failures should stop
# immediately so database/network/configuration errors stay visible.
echo "Running database migrations..."
MIGRATION_LOG="$(mktemp)"
if alembic upgrade head >"${MIGRATION_LOG}" 2>&1; then
    cat "${MIGRATION_LOG}"
else
    status=$?
    cat "${MIGRATION_LOG}"
    if grep -q "Can't locate revision identified by" "${MIGRATION_LOG}"; then
        echo "Migration failed - stamping database to consolidated migration head and retrying..."
        alembic stamp --purge 0001_initial_schema
        alembic upgrade head
    else
        echo "Migration failed for a reason other than a missing Alembic revision; not stamping the database." >&2
        rm -f "${MIGRATION_LOG}"
        exit "${status}"
    fi
fi
rm -f "${MIGRATION_LOG}"

echo "Starting API on 0.0.0.0:${APP_PORT} with ${WEB_WORKERS} worker(s)..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}" --workers "${WEB_WORKERS}"
