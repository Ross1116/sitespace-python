# Data Migration Guide: Supabase to Railway PostgreSQL

This guide will help you migrate data from your Supabase instance (read-only access) to your Railway PostgreSQL database.

## Prerequisites

- ✅ Read-only access to Supabase database
- ✅ Write access to Railway PostgreSQL database  
- ✅ Both databases have the same schema structure
- ✅ Python 3.7+ installed
- ✅ Network access to both databases

## Step 1: Setup Environment

### 1.1 Install Migration Dependencies

```bash
# Install required packages for migration
pip install -r migration-requirements.txt
```

### 1.2 Configure Database Connections

1. Copy the environment template:
```bash
cp migration.env.example .env
```

2. Edit `.env` file with your actual database URLs:

```env
# Supabase Database (Source - Read Only)
SUPABASE_DATABASE_URL=postgresql://postgres.your_ref:your_password@aws-0-us-east-1.pooler.supabase.com:5432/postgres

# Railway Database (Target - Write Access)
RAILWAY_DATABASE_URL=postgresql://postgres:your_password@roundhouse.proxy.rlwy.net:12345/railway
```

### 1.3 Get Your Database URLs

#### Supabase URL:
1. Go to your Supabase project dashboard
2. Navigate to Settings → Database
3. Find "Connection string" section
4. Use the "URI" format (not the individual parameters)
5. Replace `[YOUR-PASSWORD]` with your actual database password

#### Railway URL:
1. Go to your Railway project dashboard
2. Click on your PostgreSQL service
3. Go to "Connect" tab
4. Copy the "Postgres Connection URL"

## Step 2: Prepare Your Railway Database

### 2.1 Ensure Schema Exists

Make sure your Railway database has the same tables as Supabase. You can create them using your FastAPI app:

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

### 2.2 Verify Tables

The migration script will map Supabase tables to Railway tables as follows:

**Supabase → Railway Mapping:**
- `users` → `users` (direct mapping)
- `asset_master` → `assets` (main assets table from Supabase)
- `sub_contractors` → `subcontractors` (main contractors table from Supabase)
- `slot_booking` → `slot_bookings` (main bookings table from Supabase)
- `site_projects` → `site_projects` (with ARRAY→JSON conversion)
- `file_uploads` → `file_uploads` (if exists)

**Tables Skipped (duplicates/no mapping):**
- `assets` (Supabase) - skipped, using `asset_master` instead
- `subcontractors` (Supabase) - skipped, using `sub_contractors` instead  
- `slot_bookings` (Supabase) - skipped, using `slot_booking` instead
- `project_master` - no equivalent in Railway
- `site_manager` - no equivalent in Railway
- `user_project_mapping` - no equivalent in Railway

**Important Schema Differences Handled:**
- **ARRAY columns** (`contractor_project`, `booked_assets`) → converted to JSON strings
- **Column name differences** (e.g., `maintanence_startdt` → `maintenance_start_dt`)
- **Primary key differences** (string keys vs auto-increment IDs)
- **Missing columns** are automatically skipped

## Step 3: Run the Migration

### 3.1 Test Connection First

```bash
# Test database connections
python -c "
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Test Supabase connection
try:
    conn = psycopg2.connect(os.getenv('SUPABASE_DATABASE_URL'))
    print('✅ Supabase connection successful')
    conn.close()
except Exception as e:
    print(f'❌ Supabase connection failed: {e}')

# Test Railway connection
try:
    conn = psycopg2.connect(os.getenv('RAILWAY_DATABASE_URL'))
    print('✅ Railway connection successful')
    conn.close()
except Exception as e:
    print(f'❌ Railway connection failed: {e}')
"
```

### 3.2 Run the Migration

```bash
# Run the migration script
python migrate_data.py
```

The script will:
1. Connect to both databases
2. Map Supabase tables to Railway tables with schema transformation
3. Convert ARRAY columns to JSON strings
4. Handle column name differences automatically
5. Migrate data table by table in the correct order
6. Show progress for each table mapping
7. Verify the migration by comparing record counts
8. Generate a detailed log file (`migration.log`)

### 3.3 Monitor Progress

The script provides real-time progress updates:
```
2024-01-15 10:30:00 - INFO - 🚀 Starting data migration from Supabase to Railway...
2024-01-15 10:30:01 - INFO - ✅ Connected to Supabase database
2024-01-15 10:30:02 - INFO - ✅ Connected to Railway database
2024-01-15 10:30:03 - INFO - 📦 Migrating: users -> users
2024-01-15 10:30:04 - INFO - 📊 Migrating 'users' -> 'users': 1500 records
2024-01-15 10:30:05 - INFO - 📈 users -> users: 1000/1500 records (66.7%)
2024-01-15 10:30:06 - INFO - ✅ Completed migration: users -> users: 1500 records
2024-01-15 10:30:07 - INFO - 📦 Migrating: asset_master -> assets
2024-01-15 10:30:08 - INFO - 📊 Migrating 'asset_master' -> 'assets': 567 records
```

## Step 4: Verify Migration

### 4.1 Check Migration Summary

The script automatically verifies the migration and shows a summary:

```
============================================================
📊 MIGRATION SUMMARY
============================================================
⏱️  Duration: 0:05:23
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

### 4.2 Manual Verification

You can also manually verify the data:

```sql
-- Connect to your Railway database and run these queries

-- Check record counts in Railway database
SELECT 'users' as table_name, COUNT(*) as count FROM users
UNION ALL
SELECT 'assets', COUNT(*) FROM assets
UNION ALL
SELECT 'subcontractors', COUNT(*) FROM subcontractors
UNION ALL
SELECT 'slot_bookings', COUNT(*) FROM slot_bookings
UNION ALL
SELECT 'site_projects', COUNT(*) FROM site_projects;

-- Check sample data
SELECT * FROM users LIMIT 5;
SELECT * FROM site_projects LIMIT 5;
```

## Step 5: Configuration Options

### 5.1 Batch Size

You can adjust the batch size in `migrate_data.py`:

```python
BATCH_SIZE = 1000  # Increase for faster migration, decrease if you have memory issues
```

### 5.2 Clear Target Tables

If you want to clear existing data in Railway before migration:

```python
CLEAR_TARGET_TABLES = True  # Set to True to clear target tables first
```

### 5.3 Table Order

The migration respects foreign key dependencies. Tables are migrated in this order:
1. `users` (no dependencies)
2. `site_projects` (no dependencies)
3. `subcontractors` (no dependencies)
4. `assets` (no dependencies)
5. `slot_bookings` (no dependencies)
6. `file_uploads` (if exists)

## Troubleshooting

### Common Issues

#### 1. Connection Timeout
```
Error: psycopg2.OperationalError: timeout expired
```
**Solution**: Check your network connection and database URLs. Supabase might have connection limits.

#### 2. Permission Denied
```
Error: permission denied for table users
```
**Solution**: Verify your database URLs and permissions. Ensure the Supabase user has read access and Railway user has write access.

#### 3. Table Not Found
```
Error: relation "users" does not exist
```
**Solution**: Make sure your Railway database has all the required tables. Run the schema creation step again.

#### 4. Duplicate Key Error
```
Error: duplicate key value violates unique constraint
```
**Solution**: The script uses `ON CONFLICT (id) DO NOTHING` to handle duplicates. This is normal if you're re-running the migration.

### Performance Tips

1. **Large Datasets**: For very large tables (>100k records), consider increasing the batch size to 5000-10000
2. **Network Issues**: If you have connection issues, decrease the batch size to 100-500
3. **Memory Issues**: If you run out of memory, decrease the batch size to 100

### Logs and Debugging

- Check `migration.log` for detailed logs
- Set `echo=True` in database connections for SQL debugging
- Use `CLEAR_TARGET_TABLES = True` for clean re-runs

## Security Notes

- ✅ The script uses read-only connection to Supabase
- ✅ Environment variables keep credentials secure
- ✅ No data is modified in the source database
- ✅ All connections use SSL by default

## Post-Migration Steps

1. **Update Sequences**: If you have auto-incrementing IDs, update the sequences:
```sql
-- Run this in your Railway database
SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));
SELECT setval('site_projects_id_seq', (SELECT MAX(id) FROM site_projects));
SELECT setval('subcontractors_id_seq', (SELECT MAX(id) FROM subcontractors));
SELECT setval('assets_id_seq', (SELECT MAX(id) FROM assets));
SELECT setval('slot_bookings_id_seq', (SELECT MAX(id) FROM slot_bookings));
```

2. **Test Your Application**: Make sure your FastAPI app works with the migrated data

3. **Backup**: Consider creating a backup of your Railway database after successful migration

## Support

If you encounter issues:
1. Check the `migration.log` file for detailed error messages
2. Verify your database URLs and permissions
3. Ensure both databases have the same schema
4. Try running with smaller batch sizes if you have performance issues

The migration script is designed to be safe and can be run multiple times without duplicating data.
