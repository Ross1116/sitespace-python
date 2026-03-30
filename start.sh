#!/bin/bash

# Railway startup script for Sitespace FastAPI
echo "🚀 Starting Sitespace FastAPI application..."

# Set default port if not provided
export PORT=${PORT:-8080}
echo "📡 Using port: $PORT"

# --- Database Initialization and Wait ---
echo "✅ DATABASE_URL is configured"
echo "🔗 DATABASE_URL: ${DATABASE_URL:0:50}..."

WAIT_ATTEMPTS=15
WAIT_DELAY=2
DB_READY=0

echo "⏱️ Waiting for PostgreSQL to become available..."
for i in $(seq 1 $WAIT_ATTEMPTS); do
    # Try connecting to the database using psql (if available) or Python
    if python -c "
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    try:
        # Use a short connect timeout for faster retries
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
    echo "💥 FATAL: PostgreSQL failed to become available after $WAIT_ATTEMPTS attempts."
    echo "Continuing without running migrations..."
else
    echo "✅ PostgreSQL is running and accepting connections."

    # --- STANDARD MIGRATION LOGIC (SAFE) ---
    echo "🔄 Running Alembic database migrations (upgrade head)..."
    alembic upgrade head
    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Alembic command failed (upgrade head)."
        exit 1
    fi
    echo "✅ Migrations successful."

    # --- Optional one-time Stage 10 backfill hook ---
    if [ "${RUN_STAGE10_BACKFILL_ON_STARTUP}" = "true" ] || [ "${RUN_STAGE10_BACKFILL_ON_STARTUP}" = "1" ]; then
        echo "🔁 RUN_STAGE10_BACKFILL_ON_STARTUP enabled; running Stage 10 learning backfill..."
        STAGE10_BACKFILL_ARGS=""
        if [ "${STAGE10_BACKFILL_DELETE_LEGACY}" = "true" ] || [ "${STAGE10_BACKFILL_DELETE_LEGACY}" = "1" ]; then
            STAGE10_BACKFILL_ARGS="--delete-unreferenced-legacy"
            echo "🧹 Stage 10 backfill will delete unreferenced legacy null-project cache rows."
        fi

        python -m scripts.backfill_stage10_learning $STAGE10_BACKFILL_ARGS
        if [ $? -ne 0 ]; then
            echo "❌ ERROR: Stage 10 backfill failed."
            exit 1
        fi
        echo "✅ Stage 10 backfill completed successfully."
    else
        echo "ℹ️ Stage 10 backfill hook disabled."
    fi

fi

# --- Application Startup ---

# Test Python import before starting server
echo "🔍 Testing Python imports..."
# (Your Python import test remains here)
python -c "
try:
    from app.main import app
    print('✅ App import successful')
except Exception as e:
    print(f'❌ App import failed: {e}')
    import traceback
    traceback.print_exc()
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "💥 Import test failed, exiting..."
    exit 1
fi

# Start the application
echo "🎯 Starting uvicorn server..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --access-log
