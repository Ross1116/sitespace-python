#!/bin/bash

# Railway startup script for Sitespace — UPLOAD WORKER service.
# Persistent service, no HTTP. Polls for upload jobs.
echo "Starting Sitespace upload worker..."

# Ensure the worker pool sizing is used (pool_size=3, max_overflow=5)
export SERVICE_ROLE="${SERVICE_ROLE:-worker}"

# --- Database Readiness Wait ---
WAIT_ATTEMPTS=20
WAIT_DELAY=3
DB_READY=0

echo "Waiting for PostgreSQL..."
for i in $(seq 1 $WAIT_ATTEMPTS); do
    if python -c "
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL, connect_args={'connect_timeout': 5})
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        print('Database connection successful!')
        exit(0)
    except (OperationalError, ProgrammingError):
        print('Database not ready, retrying...')
        exit(1)
else:
    print('DATABASE_URL not set!')
    exit(1)
" ; then
        DB_READY=1
        break
    fi
    echo "Attempt $i/$WAIT_ATTEMPTS: Waiting $WAIT_DELAY seconds..."
    sleep $WAIT_DELAY
done

if [ $DB_READY -eq 0 ]; then
    echo "FATAL: PostgreSQL failed to become available."
    exit 1
fi

# --- Wait for schema readiness (tables must exist from web pre-deploy) ---
echo "Verifying schema..."
SCHEMA_ATTEMPTS=10
SCHEMA_DELAY=5
SCHEMA_READY=0

for i in $(seq 1 $SCHEMA_ATTEMPTS); do
    if python -c "
import os, sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
try:
    engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 5})
    with engine.connect() as conn:
        conn.execute(text('SELECT 1 FROM programme_upload_jobs LIMIT 0'))
        conn.execute(text('SELECT 1 FROM alembic_version LIMIT 1'))
    print('Schema verified.')
    sys.exit(0)
except OperationalError as e:
    print(f'Connection failed: {e}')
    sys.exit(1)
except ProgrammingError as e:
    print(f'Missing table or schema issue: {e}')
    sys.exit(1)
" ; then
        SCHEMA_READY=1
        break
    fi
    echo "Schema not ready yet, waiting $SCHEMA_DELAY seconds (attempt $i/$SCHEMA_ATTEMPTS)..."
    sleep $SCHEMA_DELAY
done

if [ $SCHEMA_READY -eq 0 ]; then
    echo "FATAL: Required tables not found. Ensure web service pre-deploy migration has run."
    exit 1
fi

echo "PostgreSQL is ready, schema verified."

# --- Start worker loop ---
exec python -m app.worker.upload_worker
