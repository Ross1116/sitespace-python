#!/usr/bin/env bash
set -e

BASE="${BASE_URL:-}"
EMAIL="${ADMIN_EMAIL:-}"
PASS="${ADMIN_PASS:-}"

if [ -z "$BASE" ] || [ -z "$EMAIL" ] || [ -z "$PASS" ]; then
  echo "❌ Missing required env vars: BASE_URL, ADMIN_EMAIL, ADMIN_PASS"
  exit 1
fi

get_booking_date() {
  os_name="$(uname -s)"
  if [ "$os_name" = "Darwin" ]; then
    date -v +7d +%F
  elif [ "$os_name" = "Linux" ]; then
    date -d '+7 days' +%F
  else
    if command -v python3 >/dev/null 2>&1; then
      python3 - <<'PY'
from datetime import date, timedelta
print((date.today() + timedelta(days=7)).strftime("%Y-%m-%d"))
PY
    elif command -v perl >/dev/null 2>&1; then
      perl -MPOSIX -e 'use Time::Piece; print((localtime(time()+7*24*60*60))->strftime("%F"))'
    else
      echo "❌ Unable to compute booking date (+7 days). Install python3 or perl." >&2
      return 1
    fi
  fi
}

BOOKING_DATE="$(get_booking_date)" || exit 1

echo "➡️ Registering admin (ignore errors if exists)..."
REGISTER_RESPONSE=$(curl -s -X POST "$BASE/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$EMAIL\",
    \"password\": \"$PASS\",
    \"confirm_password\": \"$PASS\",
    \"first_name\": \"Staging\",
    \"last_name\": \"Admin\"
  }")

echo "$REGISTER_RESPONSE"

echo "➡️ Logging in..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$EMAIL\",
    \"password\": \"$PASS\"
  }")

TOKEN=$(echo "$LOGIN_RESPONSE" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

if [ -z "$TOKEN" ]; then
  echo "❌ Login failed"
  echo "$LOGIN_RESPONSE"
  echo
  echo "⚠️  Likely causes:"
  echo "  - user is inactive (email not verified)"
  echo "  - user role is not admin"
  echo
  echo "👉 Fix by running in psql:"
  echo "UPDATE users SET is_active = true, role = 'admin' WHERE email = '$EMAIL';"
  exit 1
fi

echo "✅ Token acquired"

echo "➡️ Creating project..."
PROJECT_RESPONSE=$(curl -s -X POST "$BASE/api/projects/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Locust Test Project",
    "location": "Melbourne"
  }')

PROJECT_ID=$(echo "$PROJECT_RESPONSE" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

if [ -z "$PROJECT_ID" ]; then
  echo "❌ Failed to create project"
  echo "$PROJECT_RESPONSE"
  exit 1
fi

echo "✅ Project ID: $PROJECT_ID"

echo "➡️ Creating assets..."

ASSET1_RESPONSE=$(curl -s -X POST "$BASE/api/assets/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"$PROJECT_ID\",
    \"asset_code\": \"LOCUST-001\",
    \"name\": \"Excavator\",
    \"type\": \"EQUIPMENT\"
  }")

ASSET1_ID=$(echo "$ASSET1_RESPONSE" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

ASSET2_RESPONSE=$(curl -s -X POST "$BASE/api/assets/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"$PROJECT_ID\",
    \"asset_code\": \"LOCUST-002\",
    \"name\": \"Forklift\",
    \"type\": \"EQUIPMENT\"
  }")

ASSET2_ID=$(echo "$ASSET2_RESPONSE" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

if [ -z "$ASSET1_ID" ] || [ -z "$ASSET2_ID" ]; then
  echo "❌ Failed to create assets"
  echo "$ASSET1_RESPONSE"
  echo "$ASSET2_RESPONSE"
  exit 1
fi

echo "✅ Assets created:"
echo "  - $ASSET1_ID"
echo "  - $ASSET2_ID"

echo "➡️ Creating seed booking..."

BOOKING_RESPONSE=$(curl -s -X POST "$BASE/api/bookings/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"$PROJECT_ID\",
    \"asset_id\": \"$ASSET1_ID\",
    \"booking_date\": \"$BOOKING_DATE\",
    \"start_time\": \"09:00\",
    \"end_time\": \"17:00\",
    \"purpose\": \"Seed booking\",
    \"notes\": \"Seeded via seed.sh\"
  }")

echo "$BOOKING_RESPONSE"

echo
echo "✅ SEEDING COMPLETE"
echo
echo "👉 Verify with:"
echo "curl -H \"Authorization: Bearer \$TOKEN\" \\"
echo "  \"$BASE/api/projects/?my_projects=true&skip=0&limit=100\""
