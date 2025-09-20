#!/usr/bin/env python3
"""
Database Connection Test Script
==============================

This script tests connections to both Supabase and Railway databases
before running the full migration.

Usage:
    python test_connections.py
"""

import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_connection(db_url: str, db_name: str) -> bool:
    """Test connection to a database"""
    try:
        print(f"🔌 Testing connection to {db_name}...")
        
        # Connect to database
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        
        # Test with a simple query
        with conn.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            print(f"✅ {db_name} connection successful!")
            print(f"   Database version: {version[:50]}...")
        
        # Test table access
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                ORDER BY table_name
            """)
            tables = [row['table_name'] for row in cursor.fetchall()]
            print(f"   Available tables: {', '.join(tables)}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ {db_name} connection failed: {e}")
        return False

def test_table_counts():
    """Test and compare table counts between databases"""
    supabase_url = os.getenv('SUPABASE_DATABASE_URL')
    railway_url = os.getenv('RAILWAY_DATABASE_URL')
    
    if not supabase_url or not railway_url:
        print("❌ Database URLs not configured")
        return
    
    # Define table mappings (Supabase -> Railway)
    table_mappings = [
        ('users', 'users'),
        ('asset_master', 'assets'),
        ('sub_contractors', 'subcontractors'),
        ('slot_booking', 'slot_bookings'),
        ('site_projects', 'site_projects'),
        ('file_uploads', 'file_uploads')
    ]
    
    print("\n📊 Comparing table counts:")
    print("-" * 50)
    
    try:
        # Connect to both databases
        supabase_conn = psycopg2.connect(supabase_url, cursor_factory=RealDictCursor)
        railway_conn = psycopg2.connect(railway_url, cursor_factory=RealDictCursor)
        
        for supabase_table, railway_table in table_mappings:
            try:
                # Get Supabase count
                with supabase_conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {supabase_table}")
                    supabase_count = cursor.fetchone()[0]
                
                # Get Railway count  
                with railway_conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {railway_table}")
                    railway_count = cursor.fetchone()[0]
                
                status = "✅" if supabase_count == railway_count else "⚠️"
                table_display = f"{supabase_table}->{railway_table}"
                print(f"{status} {table_display:25} | Supabase: {supabase_count:6} | Railway: {railway_count:6}")
                
            except Exception as e:
                table_display = f"{supabase_table}->{railway_table}"
                print(f"❌ {table_display:25} | Error: {e}")
        
        supabase_conn.close()
        railway_conn.close()
        
    except Exception as e:
        print(f"❌ Failed to compare tables: {e}")

def main():
    """Main function"""
    print("🧪 Database Connection Test")
    print("=" * 40)
    
    # Check environment variables
    supabase_url = os.getenv('SUPABASE_DATABASE_URL')
    railway_url = os.getenv('RAILWAY_DATABASE_URL')
    
    if not supabase_url:
        print("❌ SUPABASE_DATABASE_URL not set")
        print("Please set this environment variable in your .env file")
        sys.exit(1)
    
    if not railway_url:
        print("❌ RAILWAY_DATABASE_URL not set")
        print("Please set this environment variable in your .env file")
        sys.exit(1)
    
    # Test connections
    supabase_ok = test_connection(supabase_url, "Supabase")
    railway_ok = test_connection(railway_url, "Railway")
    
    print("\n" + "=" * 40)
    
    if supabase_ok and railway_ok:
        print("🎉 All database connections successful!")
        print("✅ You can proceed with the migration")
        
        # Show table counts comparison
        test_table_counts()
        
    else:
        print("❌ Some database connections failed")
        print("Please check your database URLs and try again")
        sys.exit(1)

if __name__ == "__main__":
    main()
