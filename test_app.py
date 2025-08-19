#!/usr/bin/env python3
"""
Simple test script to verify the FastAPI application
"""
import requests
import json

BASE_URL = "http://localhost:8080"

def test_health_check():
    """Test the health check endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"Health Check Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Health check failed: {e}")
        return False

def test_root_endpoint():
    """Test the root endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/")
        print(f"Root Endpoint Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Root endpoint failed: {e}")
        return False

def test_docs_endpoint():
    """Test the docs endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/docs")
        print(f"Docs Endpoint Status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"Docs endpoint failed: {e}")
        return False

def test_signup():
    """Test user signup"""
    try:
        signup_data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "testpassword123",
            "user_phone": "1234567890",
            "role": "user"
        }
        response = requests.post(f"{BASE_URL}/api/auth/signup", json=signup_data)
        print(f"Signup Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Signup failed: {e}")
        return False

def test_signin():
    """Test user signin"""
    try:
        signin_data = {
            "username": "testuser",
            "password": "testpassword123"
        }
        response = requests.post(f"{BASE_URL}/api/auth/signin", json=signin_data)
        print(f"Signin Status: {response.status_code}")
        if response.status_code == 200:
            token_data = response.json()
            print(f"Token received: {token_data['access_token'][:20]}...")
            return token_data['access_token']
        else:
            print(f"Response: {response.json()}")
            return None
    except Exception as e:
        print(f"Signin failed: {e}")
        return None

def test_asset_endpoints(token):
    """Test asset endpoints with authentication"""
    if not token:
        print("No token available for asset tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test asset creation
    try:
        asset_data = {
            "asset_project": "Test Project",
            "asset_title": "Test Asset",
            "asset_location": "Test Location",
            "asset_status": "active",
            "asset_poc": "Test POC",
            "usage_instructions": "Test instructions"
        }
        response = requests.post(f"{BASE_URL}/api/Asset/saveAsset", json=asset_data, headers=headers)
        print(f"Asset Creation Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Asset creation failed: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Testing Sitespace FastAPI Application")
    print("=" * 50)
    
    # Test basic endpoints
    print("\n1. Testing basic endpoints...")
    health_ok = test_health_check()
    root_ok = test_root_endpoint()
    docs_ok = test_docs_endpoint()
    
    if not all([health_ok, root_ok, docs_ok]):
        print("❌ Basic endpoint tests failed. Make sure the server is running.")
        return
    
    print("✅ Basic endpoints working!")
    
    # Test authentication
    print("\n2. Testing authentication...")
    signup_ok = test_signup()
    if signup_ok:
        print("✅ Signup working!")
    else:
        print("⚠️  Signup failed (user might already exist)")
    
    token = test_signin()
    if token:
        print("✅ Signin working!")
    else:
        print("❌ Signin failed")
        return
    
    # Test authenticated endpoints
    print("\n3. Testing authenticated endpoints...")
    asset_ok = test_asset_endpoints(token)
    if asset_ok:
        print("✅ Asset endpoints working!")
    else:
        print("❌ Asset endpoints failed")
    
    print("\n" + "=" * 50)
    print("🎉 Testing completed!")

if __name__ == "__main__":
    main()

