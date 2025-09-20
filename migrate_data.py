#!/usr/bin/env python3
"""
Data Migration Script: Supabase to Railway PostgreSQL
====================================================

This script migrates data from a Supabase instance (read-only access) 
to a Railway PostgreSQL database (write access).

Requirements:
- Read-only access to Supabase database
- Write access to Railway PostgreSQL database
- Both databases should have the same schema structure

Usage:
    python migrate_data.py

Make sure to set up your environment variables before running:
- SUPABASE_DATABASE_URL: Connection string for Supabase (read-only)
- RAILWAY_DATABASE_URL: Connection string for Railway PostgreSQL (write)
"""

import os
import sys
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class DatabaseMigrator:
    """Handles data migration between two PostgreSQL databases"""
    
    def __init__(self):
        self.source_conn = None
        self.target_conn = None
        self.migration_stats = {
            'tables_migrated': 0,
            'total_records': 0,
            'errors': [],
            'start_time': datetime.now()
        }
        
        # Define table migration order (respecting foreign key dependencies)
        # Map Supabase tables to Railway tables
        self.table_mappings = {
            # Supabase table -> Railway table
            'users': 'users',
            'project_master': None,  # No equivalent in Railway - skip or handle separately
            'asset_master': 'assets',  # Main assets table in Supabase
            'assets': None,  # Duplicate/different version - skip
            'sub_contractors': 'subcontractors',  # Main contractors table in Supabase
            'subcontractors': None,  # Duplicate/different version - skip
            'slot_booking': 'slot_bookings',  # Main bookings table in Supabase
            'slot_bookings': None,  # Duplicate/different version - skip
            'site_projects': 'site_projects',
            'site_manager': None,  # No equivalent in Railway
            'user_project_mapping': None,  # No equivalent in Railway
            'file_uploads': 'file_uploads'
        }
        
        # Define migration order (source table -> target table)
        self.migration_order = [
            ('users', 'users'),
            ('asset_master', 'assets'),
            ('sub_contractors', 'subcontractors'), 
            ('slot_booking', 'slot_bookings'),
            ('site_projects', 'site_projects'),
            ('file_uploads', 'file_uploads')
        ]
    
    def connect_databases(self):
        """Establish connections to source (Supabase) and target (Railway) databases"""
        try:
            # Source database (Supabase - read-only)
            supabase_url = os.getenv('SUPABASE_DATABASE_URL')
            if not supabase_url:
                raise ValueError("SUPABASE_DATABASE_URL environment variable not set")
            
            logger.info("Connecting to Supabase database...")
            self.source_conn = psycopg2.connect(
                supabase_url,
                cursor_factory=RealDictCursor,
                options='-c default_transaction_read_only=on'  # Ensure read-only
            )
            logger.info("✅ Connected to Supabase database")
            
            # Target database (Railway - write access)
            railway_url = os.getenv('RAILWAY_DATABASE_URL')
            if not railway_url:
                raise ValueError("RAILWAY_DATABASE_URL environment variable not set")
            
            logger.info("Connecting to Railway database...")
            self.target_conn = psycopg2.connect(railway_url, cursor_factory=RealDictCursor)
            logger.info("✅ Connected to Railway database")
            
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            raise
    
    def get_column_mappings(self, source_table: str, target_table: str) -> Dict[str, str]:
        """Get column mappings between source and target tables"""
        mappings = {
            # users table - mostly compatible
            'users': {
                'username': 'username',
                'email': 'email', 
                'password': 'password',
                'user_id': 'user_id',
                'user_phone': 'user_phone',
                'profile_pic': 'profile_pic',
                'credit_point': 'credit_point',
                'role': 'role',
                'dob': 'dob',
                'is_active': 'is_active',
                'created_at': 'created_at',
                'updated_at': 'updated_at'
            },
            
            # asset_master -> assets
            'asset_master': {
                'asset_key': 'asset_key',
                'asset_project': 'asset_project',
                'asset_title': 'asset_title',
                'asset_location': 'asset_location',
                'asset_status': 'asset_status',
                'asset_poc': 'asset_poc',
                'maintanence_startdt': 'maintenance_start_dt',  # Note: typo fix
                'maintanence_enddt': 'maintenance_end_dt',      # Note: typo fix
                'usage_instructions': 'usage_instructions',
                'created_dt': 'created_at'
            },
            
            # sub_contractors -> subcontractors
            'sub_contractors': {
                'contractor_key': None,  # Skip - Railway uses auto-increment ID
                'contractor_name': 'contractor_name',
                'contractor_company': 'contractor_company',
                'contractor_trade': 'contractor_trade',
                'contractor_email': 'contractor_email',
                'contractor_phone': 'contractor_phone',
                'created_by': 'created_by',
                'created_dt': 'created_at'
                # Skip space_id_ref - not in Railway schema
            },
            
            # slot_booking -> slot_bookings
            'slot_booking': {
                'booking_key': 'booking_key',
                'booking_project': 'booking_project',
                'booking_title': 'booking_title',
                'booking_for': 'booking_for',
                'booked_assets': 'booked_assets',  # Will need array conversion
                'booking_status': 'booking_status',
                'booking_timedt': 'booking_time_dt',
                'booking_duration_mins': 'booking_duration_mins',
                'booking_description': 'booking_description',
                'booking_notes': 'booking_notes',
                'booking_created_by': 'booking_created_by',
                'booking_created_dt': 'created_at'
            },
            
            # site_projects - mostly compatible but has arrays
            'site_projects': {
                'contractor_key': 'contractor_key',
                'email_id': 'email_id',
                'contractor_project': 'contractor_project',  # Will need array conversion
                'contractor_project_id': 'contractor_project_id',
                'contractor_name': 'contractor_name',
                'contractor_company': 'contractor_company',
                'contractor_trade': 'contractor_trade',
                'contractor_email': 'contractor_email',
                'contractor_phone': 'contractor_phone',
                'created_by': 'created_by',
                'created_at': 'created_at',
                'updated_at': 'updated_at'
            }
        }
        
        return mappings.get(source_table, {})
    
    def convert_array_to_json(self, value) -> str:
        """Convert PostgreSQL array to JSON string for SQLite compatibility"""
        if value is None:
            return None
        if isinstance(value, list):
            return json.dumps(value)
        if isinstance(value, str) and value.startswith('{') and value.endswith('}'):
            # PostgreSQL array format: {item1,item2,item3}
            items = value[1:-1].split(',') if len(value) > 2 else []
            return json.dumps(items)
        return str(value)
    
    def transform_row_data(self, row_data: Dict, source_table: str, target_table: str) -> Dict:
        """Transform row data from source schema to target schema"""
        column_mappings = self.get_column_mappings(source_table, target_table)
        transformed_data = {}
        
        for source_col, target_col in column_mappings.items():
            if target_col is None:  # Skip columns that don't exist in target
                continue
                
            if source_col in row_data:
                value = row_data[source_col]
                
                # Handle array columns
                if source_col in ['contractor_project', 'booked_assets']:
                    value = self.convert_array_to_json(value)
                
                # Handle date/timestamp conversions
                elif source_col in ['maintanence_startdt', 'maintanence_enddt']:
                    # Convert date to string format expected by Railway
                    if value:
                        value = str(value)
                
                elif source_col == 'booking_timedt':
                    # Convert timestamp to string format
                    if value:
                        value = str(value)
                
                transformed_data[target_col] = value
        
        return transformed_data

    def get_table_schema(self, table_name: str) -> List[str]:
        """Get column names for a table from the source database"""
        try:
            with self.source_conn.cursor() as cursor:
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = %s 
                    ORDER BY ordinal_position
                """, (table_name,))
                
                columns = [row['column_name'] for row in cursor.fetchall()]
                logger.info(f"📋 Table '{table_name}' has columns: {columns}")
                return columns
                
        except Exception as e:
            logger.error(f"❌ Failed to get schema for table '{table_name}': {e}")
            return []
    
    def check_table_exists(self, table_name: str, connection) -> bool:
        """Check if a table exists in the database"""
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    )
                """, (table_name,))
                
                return cursor.fetchone()[0]
                
        except Exception as e:
            logger.error(f"❌ Failed to check if table '{table_name}' exists: {e}")
            return False
    
    def get_table_count(self, table_name: str) -> int:
        """Get the number of records in a table"""
        try:
            with self.source_conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                return count
                
        except Exception as e:
            logger.error(f"❌ Failed to count records in table '{table_name}': {e}")
            return 0
    
    def clear_target_table(self, table_name: str):
        """Clear all data from target table (optional - for clean migration)"""
        try:
            with self.target_conn.cursor() as cursor:
                # Disable foreign key checks temporarily if needed
                cursor.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE")
                self.target_conn.commit()
                logger.info(f"🧹 Cleared target table '{table_name}'")
                
        except Exception as e:
            logger.warning(f"⚠️  Could not clear table '{table_name}': {e}")
            # Continue anyway - table might not exist or might have data we want to keep
    
    def migrate_table_data(self, source_table: str, target_table: str, batch_size: int = 1000, clear_target: bool = False):
        """Migrate data from source table to target table with schema transformation"""
        try:
            # Check if table exists in both databases
            if not self.check_table_exists(source_table, self.source_conn):
                logger.warning(f"⚠️  Source table '{source_table}' does not exist")
                return
            
            if not self.check_table_exists(target_table, self.target_conn):
                logger.warning(f"⚠️  Target table '{target_table}' does not exist")
                return
            
            # Get source table schema
            source_columns = self.get_table_schema(source_table)
            if not source_columns:
                logger.error(f"❌ No columns found for source table '{source_table}'")
                return
            
            # Get column mappings
            column_mappings = self.get_column_mappings(source_table, target_table)
            if not column_mappings:
                logger.error(f"❌ No column mappings found for {source_table} -> {target_table}")
                return
            
            # Get target columns (only mapped ones)
            target_columns = [target_col for target_col in column_mappings.values() if target_col is not None]
            
            # Get total record count
            total_records = self.get_table_count(source_table)
            logger.info(f"📊 Migrating '{source_table}' -> '{target_table}': {total_records} records")
            
            if total_records == 0:
                logger.info(f"✅ Source table '{source_table}' is empty, skipping")
                return
            
            # Clear target table if requested
            if clear_target:
                self.clear_target_table(target_table)
            
            # Migrate data in batches
            migrated_count = 0
            offset = 0
            
            # Determine primary key for ordering
            primary_key = 'id' if 'id' in source_columns else list(source_columns)[0]
            
            while offset < total_records:
                try:
                    # Fetch batch from source
                    with self.source_conn.cursor() as source_cursor:
                        source_columns_str = ', '.join(source_columns)
                        query = f"SELECT {source_columns_str} FROM {source_table} ORDER BY {primary_key} LIMIT %s OFFSET %s"
                        source_cursor.execute(query, (batch_size, offset))
                        batch_data = source_cursor.fetchall()
                    
                    if not batch_data:
                        break
                    
                    # Transform batch data
                    transformed_batch = []
                    for row in batch_data:
                        transformed_row = self.transform_row_data(dict(row), source_table, target_table)
                        if transformed_row:  # Only add if transformation succeeded
                            transformed_batch.append(transformed_row)
                    
                    if not transformed_batch:
                        logger.warning(f"⚠️  No valid data in batch at offset {offset}")
                        offset += batch_size
                        continue
                    
                    # Insert batch into target
                    with self.target_conn.cursor() as target_cursor:
                        # Prepare insert query
                        target_columns_str = ', '.join(target_columns)
                        placeholders = ', '.join(['%s'] * len(target_columns))
                        
                        # Use different conflict resolution based on table
                        if target_table == 'users':
                            conflict_clause = "ON CONFLICT (id) DO NOTHING"
                        elif 'id' in [col.lower() for col in target_columns]:
                            conflict_clause = "ON CONFLICT (id) DO NOTHING"
                        else:
                            conflict_clause = ""  # No conflict resolution for tables without id
                        
                        insert_query = f"INSERT INTO {target_table} ({target_columns_str}) VALUES %s {conflict_clause}"
                        
                        # Convert batch data to tuples in correct column order
                        batch_tuples = []
                        for row_data in transformed_batch:
                            tuple_data = tuple(row_data.get(col) for col in target_columns)
                            batch_tuples.append(tuple_data)
                        
                        # Execute batch insert
                        execute_values(
                            target_cursor,
                            insert_query,
                            batch_tuples,
                            template=f"({placeholders})",
                            page_size=batch_size
                        )
                        
                        self.target_conn.commit()
                    
                    migrated_count += len(transformed_batch)
                    offset += batch_size
                    
                    # Progress logging
                    progress = (migrated_count / total_records) * 100
                    logger.info(f"📈 {source_table} -> {target_table}: {migrated_count}/{total_records} records ({progress:.1f}%)")
                    
                except Exception as batch_error:
                    logger.error(f"❌ Batch migration failed for {source_table} -> {target_table} at offset {offset}: {batch_error}")
                    self.migration_stats['errors'].append(f"Table {source_table}->{target_table} batch error: {batch_error}")
                    # Continue with next batch
                    offset += batch_size
                    continue
            
            logger.info(f"✅ Completed migration: {source_table} -> {target_table}: {migrated_count} records")
            self.migration_stats['total_records'] += migrated_count
            self.migration_stats['tables_migrated'] += 1
            
        except Exception as e:
            logger.error(f"❌ Failed to migrate {source_table} -> {target_table}: {e}")
            self.migration_stats['errors'].append(f"Table {source_table}->{target_table}: {e}")
    
    def verify_migration(self):
        """Verify that migration was successful by comparing record counts"""
        logger.info("🔍 Verifying migration...")
        
        verification_results = {}
        
        for source_table, target_table in self.migration_order:
            try:
                # Get count from source
                with self.source_conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {source_table}")
                    source_count = cursor.fetchone()[0]
                
                # Get count from target
                with self.target_conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {target_table}")
                    target_count = cursor.fetchone()[0]
                
                table_key = f"{source_table}->{target_table}"
                verification_results[table_key] = {
                    'source': source_count,
                    'target': target_count,
                    'match': source_count == target_count
                }
                
                if source_count == target_count:
                    logger.info(f"✅ {source_table}->{target_table}: {source_count} records (MATCH)")
                else:
                    logger.warning(f"⚠️  {source_table}->{target_table}: Source={source_count}, Target={target_count} (MISMATCH)")
                    
            except Exception as e:
                logger.error(f"❌ Verification failed for {source_table}->{target_table}: {e}")
                verification_results[f"{source_table}->{target_table}"] = {'error': str(e)}
        
        return verification_results
    
    def run_migration(self, batch_size: int = 1000, clear_target_tables: bool = False):
        """Run the complete migration process"""
        try:
            logger.info("🚀 Starting data migration from Supabase to Railway...")
            
            # Connect to databases
            self.connect_databases()
            
            # Migrate each table in order
            for source_table, target_table in self.migration_order:
                logger.info(f"📦 Migrating: {source_table} -> {target_table}")
                self.migrate_table_data(source_table, target_table, batch_size, clear_target_tables)
            
            # Verify migration
            verification_results = self.verify_migration()
            
            # Print summary
            self.print_migration_summary(verification_results)
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            raise
        finally:
            # Close connections
            if self.source_conn:
                self.source_conn.close()
                logger.info("🔌 Closed Supabase connection")
            
            if self.target_conn:
                self.target_conn.close()
                logger.info("🔌 Closed Railway connection")
    
    def print_migration_summary(self, verification_results: Dict):
        """Print a summary of the migration results"""
        end_time = datetime.now()
        duration = end_time - self.migration_stats['start_time']
        
        logger.info("\n" + "="*60)
        logger.info("📊 MIGRATION SUMMARY")
        logger.info("="*60)
        logger.info(f"⏱️  Duration: {duration}")
        logger.info(f"📋 Tables migrated: {self.migration_stats['tables_migrated']}")
        logger.info(f"📊 Total records: {self.migration_stats['total_records']}")
        
        if self.migration_stats['errors']:
            logger.info(f"❌ Errors: {len(self.migration_stats['errors'])}")
            for error in self.migration_stats['errors']:
                logger.error(f"   - {error}")
        else:
            logger.info("✅ No errors occurred")
        
        logger.info("\n📋 VERIFICATION RESULTS:")
        for table, result in verification_results.items():
            if 'error' in result:
                logger.error(f"   ❌ {table}: {result['error']}")
            elif result['match']:
                logger.info(f"   ✅ {table}: {result['source']} records")
            else:
                logger.warning(f"   ⚠️  {table}: Source={result['source']}, Target={result['target']}")
        
        logger.info("="*60)


def main():
    """Main function to run the migration"""
    
    # Check environment variables
    required_vars = ['SUPABASE_DATABASE_URL', 'RAILWAY_DATABASE_URL']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {missing_vars}")
        logger.info("Please set the following environment variables:")
        logger.info("- SUPABASE_DATABASE_URL: Your Supabase database connection string")
        logger.info("- RAILWAY_DATABASE_URL: Your Railway PostgreSQL connection string")
        sys.exit(1)
    
    # Configuration
    BATCH_SIZE = 1000  # Adjust based on your data size and memory constraints
    CLEAR_TARGET_TABLES = False  # Set to True if you want to clear target tables first
    
    # Run migration
    migrator = DatabaseMigrator()
    
    try:
        migrator.run_migration(
            batch_size=BATCH_SIZE,
            clear_target_tables=CLEAR_TARGET_TABLES
        )
        logger.info("🎉 Migration completed successfully!")
        
    except Exception as e:
        logger.error(f"💥 Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
