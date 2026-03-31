#!/bin/bash

# Railway startup script for Sitespace — NIGHTLY LOOKAHEAD tick.
# Runs as a Railway scheduled job (cron: */5 * * * *).
# Executes one tick and exits cleanly.
echo "Starting Sitespace nightly tick..."

# --- Database Readiness Wait (shorter — Railway cron has limited runtime) ---
WAIT_ATTEMPTS=10
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

# --- Wait for schema readiness ---
echo "Verifying schema..."
python -c "
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 5})
with engine.connect() as conn:
    conn.execute(text('SELECT 1 FROM scheduled_job_runs LIMIT 0'))
    conn.execute(text('SELECT 1 FROM alembic_version LIMIT 1'))
print('Schema verified.')
" || { echo "FATAL: Required tables not found."; exit 1; }

echo "PostgreSQL ready, executing nightly tick..."

# --- Execute one tick and exit ---
exec python -m app.worker.nightly_tick
