#!/usr/bin/env python3
"""
Migration Setup Script
=====================

This script helps you set up the migration environment and ensures
your Railway database has the correct schema before migrating data.

Usage:
    python setup_migration.py
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def create_env_file():
    """Create .env file from template if it doesn't exist"""
    if os.path.exists('.env'):
        print("✅ .env file already exists")
        return True
    
    if os.path.exists('migration.env.example'):
        print("📝 Creating .env file from template...")
        with open('migration.env.example', 'r') as template:
            content = template.read()
        
        with open('.env', 'w') as env_file:
            env_file.write(content)
        
        print("✅ .env file created")
        print("⚠️  Please edit .env file with your actual database URLs")
        return False
    else:
        print("❌ migration.env.example not found")
        return False

def install_requirements():
    """Install migration requirements"""
    print("📦 Installing migration requirements...")
    os.system("pip install -r migration-requirements.txt")
    print("✅ Requirements installed")

def create_railway_schema():
    """Create tables in Railway database using FastAPI models"""
    try:
        print("🏗️  Creating database schema in Railway...")
        
        # Import your FastAPI models and create tables
        sys.path.append('.')
        from app.core.database import engine, Base
        from app.models import User, Asset, SiteProject, Subcontractor, SlotBooking
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        print("✅ Database schema created successfully")
        return True
        
    except Exception as e:
        print(f"❌ Failed to create schema: {e}")
        print("Please make sure your RAILWAY_DATABASE_URL is correct in .env")
        return False

def main():
    """Main setup function"""
    print("🚀 Migration Setup")
    print("=" * 30)
    
    # Step 1: Create .env file
    env_ready = create_env_file()
    
    if not env_ready:
        print("\n⚠️  Please edit the .env file with your database URLs and run this script again")
        return
    
    # Step 2: Install requirements
    install_requirements()
    
    # Step 3: Create Railway schema
    schema_ready = create_railway_schema()
    
    if schema_ready:
        print("\n🎉 Setup completed successfully!")
        print("Next steps:")
        print("1. Run: python test_connections.py")
        print("2. Run: python migrate_data.py")
    else:
        print("\n❌ Setup failed. Please check your configuration.")

if __name__ == "__main__":
    main()


