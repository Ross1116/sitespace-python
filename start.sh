#!/bin/bash

# Railway startup script for Sitespace FastAPI
echo "🚀 Starting Sitespace FastAPI application..."

# Set default port if not provided
export PORT=${PORT:-8080}
echo "📡 Using port: $PORT"

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "⚠️  WARNING: DATABASE_URL not set, using SQLite fallback"
else
    echo "✅ DATABASE_URL is configured"
    echo "🔗 DATABASE_URL: ${DATABASE_URL:0:50}..." # Show first 50 chars for debugging
fi

# Check if required environment variables are set
if [ -z "$JWT_SECRET" ]; then
    echo "⚠️  WARNING: JWT_SECRET not set, using default (not secure for production)"
else
    echo "✅ JWT_SECRET is set"
fi

if [ -z "$SECRET_KEY" ]; then
    echo "⚠️  WARNING: SECRET_KEY not set, using default (not secure for production)"
else
    echo "✅ SECRET_KEY is set"
fi

# --- START: DATABASE MIGRATION & WAIT FIX ---

# The Railway-provided DATABASE_URL usually points directly to the 'sitespace' database.
# Alembic needs to connect, and sometimes the database is still starting up.
if [ -n "$DATABASE_URL" ]; then
    echo "⏱️ Waiting for PostgreSQL to become available..."
    
    # Extract host, port, user, and password from the URL for psql connection test
    # This uses a simple awk-based parser, assuming a standard postgresql://user:pass@host:port/dbname format
    DB_HOST=$(echo "$DATABASE_URL" | awk -F'[@:/]' '{print $5}')
    DB_PORT=$(echo "$DATABASE_URL" | awk -F'[@:/]' '{print $6}' | cut -d/ -f1)
    DB_USER=$(echo "$DATABASE_URL" | awk -F'[@:/]' '{print $3}')
    DB_PASS=$(echo "$DATABASE_URL" | awk -F'[@:/]' '{print $4}')
    
    # Use PING (or a simple loop) to check connectivity
    # This loop attempts to connect to the DB using a small Python script
    MAX_RETRIES=15
    RETRY_COUNT=0
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        # Use Python to try and open a connection, which is more reliable than psql when credentials are complex
        python -c "
import os
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
try:
    engine = create_engine(os.environ['DATABASE_URL'])
    with engine.connect():
        print('Database connection successful!')
        exit(0)
except OperationalError:
    print('Database not ready, retrying...')
    exit(1)
"
        if [ $? -eq 0 ]; then
            echo "✅ PostgreSQL is running and accepting connections."
            break
        fi

        RETRY_COUNT=$((RETRY_COUNT+1))
        echo "Attempt $RETRY_COUNT/$MAX_RETRIES: Waiting 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "❌ ERROR: PostgreSQL did not start in time. Deployment aborted."
        exit 1
    fi
    
    # Run database migrations
    echo "🔄 Running Alembic database migrations..."
    alembic upgrade head
    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Alembic migration failed."
        exit 1
    fi
    echo "✅ Migrations complete."
fi

# --- END: DATABASE MIGRATION & WAIT FIX ---


# Test Python import before starting server
echo "🔍 Testing Python imports..."
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

if [ $? -ne 0 ]; then/
    echo "💥 Import test failed, exiting..."
    exit 1
fi

# Start the application
echo "🎯 Starting uvicorn server..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --access-log
