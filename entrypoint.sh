#!/bin/sh
set -e

# Run migrations. If Alembic can't find the current revision (e.g. after
# migration consolidation where old revision IDs were removed), stamp the
# database to the new consolidated head and retry.
if ! alembic upgrade head 2>&1; then
    echo "Migration failed — stamping database to consolidated migration head and retrying..."
    alembic stamp --purge 0001_initial_schema
    alembic upgrade head
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-4}"
