#!/usr/bin/env python3
"""
API Connection Test Script
=========================

This script tests connections to Supabase API and Railway database
before running the API-based migration.

Usage:
    python test_api_connections.py
"""

import os
import sys
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_supabase_api() -> bool:
    """Test Supabase API connection"""
    try:
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_anon_key = os.getenv('SUPABASE_ANON_KEY')
        
        if not supabase_url or not supabase_anon_key:
            print("❌ Supabase API credentials not configured")
            return False
        
        print(f"🔌 Testing Supabase API connection...")
        print(f"   URL: {supabase_url}")
        
        # Setup headers
        headers = {
            'apikey': supabase_anon_key,
            'Authorization': f'Bearer {supabase_anon_key}',
            'Content-Type': 'application/json'
        }
        
        # Test API connection
        response = requests.get(
            f"{supabase_url.rstrip('/')}/rest/v1/",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Supabase API connection successful!")
        else:
            print(f"⚠️  Supabase API returned status {response.status_code}")
            print(f"   Response: {response.text[:200]}...")
        
        # Test table access
        print("🔍 Testing table access...")
        tables_to_test = ['users', 'asset_master', 'sub_contractors', 'slot_booking', 'site_projects']
        
        for table in tables_to_test:
            try:
                response = requests.head(
                    f"{supabase_url.rstrip('/')}/rest/v1/{table}",
                    headers={**headers, 'Prefer': 'count=exact'},
                    params={'limit': 1},
                    timeout=5
                )
                
                if response.status_code == 200:
                    content_range = response.headers.get('content-range', '0-0/0')
                    total = content_range.split('/')[-1]
                    print(f"   ✅ {table}: {total} records")
                elif response.status_code == 404:
                    print(f"   ⚠️  {table}: Table not found")
                else:
                    print(f"   ❌ {table}: Error {response.status_code}")
                    
            except Exception as e:
                print(f"   ❌ {table}: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ Supabase API test failed: {e}")
        return False

def test_railway_database() -> bool:
    """Test Railway database connection"""
    try:
        railway_url = os.getenv('RAILWAY_DATABASE_URL')
        
        if not railway_url:
            print("❌ RAILWAY_DATABASE_URL not configured")
            return False
        
        print(f"🔌 Testing Railway database connection...")
        
        # Connect to database
        conn = psycopg2.connect(railway_url, cursor_factory=RealDictCursor)
        
        # Test with a simple query
        with conn.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            print(f"✅ Railway database connection successful!")
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
        print(f"❌ Railway database connection failed: {e}")
        return False

def compare_table_counts():
    """Compare table counts between Supabase and Railway"""
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_anon_key = os.getenv('SUPABASE_ANON_KEY')
    railway_url = os.getenv('RAILWAY_DATABASE_URL')
    
    if not all([supabase_url, supabase_anon_key, railway_url]):
        print("❌ Missing configuration for table comparison")
        return
    
    # Table mappings (Supabase -> Railway)
    table_mappings = [
        ('users', 'users'),
        ('asset_master', 'assets'),
        ('sub_contractors', 'subcontractors'),
        ('slot_booking', 'slot_bookings'),
        ('site_projects', 'site_projects'),
        ('file_uploads', 'file_uploads')
    ]
    
    print("\n📊 Comparing table counts:")
    print("-" * 60)
    
    try:
        # Setup Supabase API
        supabase_headers = {
            'apikey': supabase_anon_key,
            'Authorization': f'Bearer {supabase_anon_key}',
            'Prefer': 'count=exact'
        }
        
        # Connect to Railway
        railway_conn = psycopg2.connect(railway_url, cursor_factory=RealDictCursor)
        
        for supabase_table, railway_table in table_mappings:
            try:
                # Get Supabase count via API
                response = requests.head(
                    f"{supabase_url.rstrip('/')}/rest/v1/{supabase_table}",
                    headers=supabase_headers,
                    params={'limit': 1},
                    timeout=5
                )
                
                if response.status_code == 200:
                    content_range = response.headers.get('content-range', '0-0/0')
                    supabase_count = int(content_range.split('/')[-1]) if content_range.split('/')[-1].isdigit() else 0
                else:
                    supabase_count = 0
                
                # Get Railway count
                with railway_conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {railway_table}")
                    railway_count = cursor.fetchone()[0]
                
                status = "✅" if supabase_count == railway_count else "⚠️"
                table_display = f"{supabase_table}->{railway_table}"
                print(f"{status} {table_display:30} | Supabase: {supabase_count:6} | Railway: {railway_count:6}")
                
            except Exception as e:
                table_display = f"{supabase_table}->{railway_table}"
                print(f"❌ {table_display:30} | Error: {e}")
        
        railway_conn.close()
        
    except Exception as e:
        print(f"❌ Failed to compare tables: {e}")

def main():
    """Main function"""
    print("🧪 API Connection Test")
    print("=" * 40)
    
    # Check environment variables
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_anon_key = os.getenv('SUPABASE_ANON_KEY')
    railway_url = os.getenv('RAILWAY_DATABASE_URL')
    
    missing_vars = []
    if not supabase_url:
        missing_vars.append('SUPABASE_URL')
    if not supabase_anon_key:
        missing_vars.append('SUPABASE_ANON_KEY')
    if not railway_url:
        missing_vars.append('RAILWAY_DATABASE_URL')
    
    if missing_vars:
        print(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        print("\nPlease set these in your .env file:")
        print("- SUPABASE_URL: Your Supabase project URL")
        print("- SUPABASE_ANON_KEY: Your Supabase anon key")
        print("- RAILWAY_DATABASE_URL: Your Railway database URL")
        sys.exit(1)
    
    # Test connections
    supabase_ok = test_supabase_api()
    railway_ok = test_railway_database()
    
    print("\n" + "=" * 40)
    
    if supabase_ok and railway_ok:
        print("🎉 All connections successful!")
        print("✅ You can proceed with the API migration")
        
        # Show table counts comparison
        compare_table_counts()
        
    else:
        print("❌ Some connections failed")
        print("Please check your configuration and try again")
        sys.exit(1)

if __name__ == "__main__":
    main()





