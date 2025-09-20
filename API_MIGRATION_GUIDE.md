# API-Based Migration Guide: Supabase to Railway PostgreSQL

This guide helps you migrate data from Supabase using the REST API (no database password required) to your Railway PostgreSQL database.

## 🎯 Why Use the API Approach?

- ✅ **No database password needed** - uses Supabase REST API
- ✅ **Works with read-only dashboard access**
- ✅ **Uses public anon key** (safe to use)
- ✅ **Handles RLS (Row Level Security)** automatically
- ✅ **No direct database connection required**

## Prerequisites

- ✅ Access to Supabase dashboard (Settings → API)
- ✅ Write access to Railway PostgreSQL database  
- ✅ Both databases have compatible schema structure
- ✅ Python 3.7+ installed
- ✅ Network access to both services

## Step 1: Get Supabase API Credentials

### 1.1 Find Your Supabase API Settings

1. Go to your Supabase project dashboard
2. Navigate to **Settings** → **API**
3. You'll see two important values:

#### Project URL
```
https://your-project-id.supabase.co
```

#### API Keys
- **anon/public key** - This is what you need (starts with `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`)
- **service_role key** - Don't use this (you probably don't have access anyway)

### 1.2 Important Notes

- ✅ The **anon key is safe to use** - it's designed for client-side access
- ✅ It respects Row Level Security (RLS) policies
- ✅ It only allows operations you're permitted to do
- ❌ **Don't use the service_role key** - that's for admin access

## Step 2: Setup Environment

### 2.1 Install API Migration Dependencies

```bash
# Install required packages for API migration
pip install -r migration-api-requirements.txt
```

### 2.2 Configure API Credentials

1. Copy the environment template:
```bash
cp migration-api.env.example .env
```

2. Edit `.env` file with your actual values:

```env
# Supabase API Configuration (No database password needed!)
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.your-actual-anon-key-here

# Railway Database (Target - Write Access)
RAILWAY_DATABASE_URL=postgresql://postgres:your_password@your_railway_host:port/railway
```

### 2.3 Get Your Railway Database URL

1. Go to your Railway project dashboard
2. Click on your PostgreSQL service
3. Go to "Connect" tab
4. Copy the "Postgres Connection URL"

## Step 3: Prepare Your Railway Database

### 3.1 Ensure Schema Exists

Make sure your Railway database has the correct tables:

```bash
# Run your FastAPI app to create tables
cd /path/to/your/fastapi/app
python -c "
from app.core.database import engine, Base
from app.models import *
Base.metadata.create_all(bind=engine)
print('✅ Tables created successfully')
"
```

### 3.2 Table Mapping

The API migration will map Supabase tables to Railway tables:

**Supabase → Railway Mapping:**
- `users` → `users` (direct mapping)
- `asset_master` → `assets` (main assets table from Supabase)
- `sub_contractors` → `subcontractors` (main contractors table from Supabase)
- `slot_booking` → `slot_bookings` (main bookings table from Supabase)
- `site_projects` → `site_projects` (with ARRAY→JSON conversion)
- `file_uploads` → `file_uploads` (if exists)

**Key Features:**
- **ARRAY columns** automatically converted to JSON strings
- **Column name differences** handled automatically
- **Primary key differences** managed (skips source IDs, uses Railway auto-increment)
- **Missing columns** automatically skipped

## Step 4: Test API Access

### 4.1 Test Connections

```bash
# Test both Supabase API and Railway database
python test_api_connections.py
```

This will:
- ✅ Test Supabase API connection
- ✅ Check table access permissions
- ✅ Test Railway database connection
- ✅ Show current record counts
- ✅ Verify table mappings

Expected output:
```
🧪 API Connection Test
========================================
🔌 Testing Supabase API connection...
   URL: https://your-project.supabase.co
✅ Supabase API connection successful!
🔍 Testing table access...
   ✅ users: 1500 records
   ✅ asset_master: 567 records
   ✅ sub_contractors: 89 records
   ✅ slot_booking: 10049 records
   ✅ site_projects: 245 records

🔌 Testing Railway database connection...
✅ Railway database connection successful!
   Available tables: users, assets, subcontractors, slot_bookings, site_projects

🎉 All connections successful!
```

## Step 5: Run the API Migration

### 5.1 Run the Migration

```bash
# Run the API-based migration script
python migrate_data_api.py
```

The script will:
1. Connect to Supabase via REST API
2. Connect to Railway PostgreSQL database
3. Map tables and transform data schema
4. Convert ARRAY columns to JSON strings
5. Handle column name differences
6. Migrate data in batches with progress tracking
7. Verify migration by comparing record counts
8. Generate detailed log file (`migration_api.log`)

### 5.2 Monitor Progress

Real-time progress updates:
```
2024-01-15 10:30:00 - INFO - 🚀 Starting data migration from Supabase API to Railway...
2024-01-15 10:30:01 - INFO - ✅ Supabase API configured successfully
2024-01-15 10:30:02 - INFO - ✅ Connected to Railway database
2024-01-15 10:30:03 - INFO - 📦 Migrating: users -> users
2024-01-15 10:30:04 - INFO - 📊 Migrating 'users' -> 'users': 1500 records
2024-01-15 10:30:05 - INFO - 📈 users -> users: 1000/1500 records (66.7%)
2024-01-15 10:30:06 - INFO - ✅ Completed migration: users -> users: 1500 records
```

## Step 6: Verify Migration

### 6.1 Automatic Verification

The script automatically verifies migration:

```
============================================================
📊 MIGRATION SUMMARY
============================================================
⏱️  Duration: 0:08:45
📋 Tables migrated: 5
📊 Total records: 12,450
✅ No errors occurred

📋 VERIFICATION RESULTS:
   ✅ users->users: 1500 records
   ✅ asset_master->assets: 567 records
   ✅ sub_contractors->subcontractors: 89 records
   ✅ slot_booking->slot_bookings: 10049 records
   ✅ site_projects->site_projects: 245 records
============================================================
```

### 6.2 Manual Verification

```sql
-- Connect to your Railway database and verify
SELECT 'users' as table_name, COUNT(*) as count FROM users
UNION ALL
SELECT 'assets', COUNT(*) FROM assets
UNION ALL
SELECT 'subcontractors', COUNT(*) FROM subcontractors
UNION ALL
SELECT 'slot_bookings', COUNT(*) FROM slot_bookings
UNION ALL
SELECT 'site_projects', COUNT(*) FROM site_projects;
```

## Step 7: Configuration Options

### 7.1 Batch Size

Adjust batch size in `migrate_data_api.py`:

```python
BATCH_SIZE = 1000  # Increase for faster migration, decrease if you hit rate limits
```

### 7.2 Clear Target Tables

```python
CLEAR_TARGET_TABLES = True  # Set to True to clear Railway tables before migration
```

### 7.3 Rate Limiting

The script includes automatic rate limiting (0.1s delay between batches) to avoid hitting Supabase API limits.

## Troubleshooting

### Common Issues

#### 1. API Authentication Error
```
Error: 401 Unauthorized
```
**Solution**: Check your SUPABASE_ANON_KEY in the .env file. Make sure you copied the full key.

#### 2. Table Not Found
```
Error: 404 Not Found for table 'users'
```
**Solution**: The table doesn't exist in Supabase or RLS policies are blocking access.

#### 3. Rate Limiting
```
Error: 429 Too Many Requests
```
**Solution**: Decrease the batch size or increase the delay between requests.

#### 4. RLS Policy Blocking Access
```
Error: No data returned from API
```
**Solution**: Check if Row Level Security policies are blocking the anon key access.

### Performance Tips

1. **Large Datasets**: Start with smaller batch sizes (100-500) to test
2. **Rate Limits**: If you hit rate limits, decrease batch size to 100
3. **Network Issues**: The script includes automatic retry logic
4. **Memory Issues**: API approach uses less memory than direct DB connection

### API Limits

- **Supabase Free Tier**: 500MB database, API rate limits apply
- **Supabase Pro**: Higher limits, better performance
- **Railway**: No specific API limits, just database connection limits

## Security Notes

- ✅ Uses read-only API access to Supabase
- ✅ Anon key is safe to use (designed for client access)
- ✅ Respects Row Level Security policies
- ✅ No database passwords required
- ✅ All connections use HTTPS/SSL

## Post-Migration Steps

### 1. Update Sequences

```sql
-- Update auto-increment sequences in Railway
SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));
SELECT setval('assets_id_seq', (SELECT MAX(id) FROM assets));
SELECT setval('subcontractors_id_seq', (SELECT MAX(id) FROM subcontractors));
SELECT setval('slot_bookings_id_seq', (SELECT MAX(id) FROM slot_bookings));
SELECT setval('site_projects_id_seq', (SELECT MAX(id) FROM site_projects));
```

### 2. Test Your Application

Make sure your FastAPI app works with the migrated data.

### 3. Backup

Create a backup of your Railway database after successful migration.

## Advantages of API Approach

- ✅ **No database credentials needed**
- ✅ **Works with dashboard-only access**
- ✅ **Respects security policies**
- ✅ **More reliable over internet connections**
- ✅ **Built-in rate limiting and error handling**
- ✅ **Can resume partial migrations**

## Files Created

- `migrate_data_api.py` - Main API migration script
- `test_api_connections.py` - Test API connections
- `migration-api-requirements.txt` - Python dependencies
- `migration-api.env.example` - Environment template
- `API_MIGRATION_GUIDE.md` - This guide

The API migration approach is perfect for your situation where you don't have database password access but can use the Supabase dashboard!


