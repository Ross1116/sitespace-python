#!/bin/bash

# Railway startup script for Sitespace FastAPI
echo "🚀 Starting Sitespace FastAPI application..."

# Set default port if not provided
export PORT=${PORT:-8080}
echo "📡 Using port: $PORT"

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "⚠️  WARNING: DATABASE_URL not set, using SQLite fallback"
else
    echo "✅ DATABASE_URL is configured"
    echo "🔗 DATABASE_URL: ${DATABASE_URL:0:50}..." # Show first 50 chars for debugging
fi

# Check if required environment variables are set
if [ -z "$JWT_SECRET" ]; then
    echo "⚠️  WARNING: JWT_SECRET not set, using default (not secure for production)"
else
    echo "✅ JWT_SECRET is set"
fi

if [ -z "$SECRET_KEY" ]; then
    echo "⚠️  WARNING: SECRET_KEY not set, using default (not secure for production)"
else
    echo "✅ SECRET_KEY is set"
fi

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

if [ $? -ne 0 ]; then
    echo "💥 Import test failed, exiting..."
    exit 1
fi

# Start the application
echo "🎯 Starting uvicorn server..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --access-log
