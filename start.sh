#!/bin/bash

# Railway startup script for Sitespace FastAPI — WEB service.
# Only the web service runs migrations; worker/nightly wait for schema.
echo "Starting Sitespace web service..."

export PORT=${PORT:-8080}
echo "Using port: $PORT"

# --- Database Readiness Wait ---
echo "DATABASE_URL: ${DATABASE_URL:0:50}..."

WAIT_ATTEMPTS=15
WAIT_DELAY=2
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
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        print('Database connection successful!')
        exit(0)
    except (OperationalError, ProgrammingError) as e:
        print(f'Database not ready, retrying...')
        exit(1)
else:
    print('DATABASE_URL not set, skipping connection check.')
    exit(0)
" ; then
        DB_READY=1
        break
    fi
    echo "Attempt $i/$WAIT_ATTEMPTS: Waiting $WAIT_DELAY seconds..."
    sleep $WAIT_DELAY
done

if [ $DB_READY -eq 0 ]; then
    echo "FATAL: PostgreSQL failed to become available after $WAIT_ATTEMPTS attempts."
    exit 1
fi

echo "PostgreSQL is ready."

# --- Run Alembic migrations (web service only) ---
echo "Running Alembic database migrations..."
alembic upgrade head
if [ $? -ne 0 ]; then
    echo "ERROR: Alembic migration failed."
    exit 1
fi
echo "Migrations successful."

# --- App import test ---
echo "Testing Python imports..."
python -c "
try:
    from app.main import app
    print('App import successful')
except Exception as e:
    print(f'App import failed: {e}')
    import traceback
    traceback.print_exc()
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "Import test failed, exiting..."
    exit 1
fi

# --- Start Uvicorn ---
echo "Starting uvicorn server..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --access-log
