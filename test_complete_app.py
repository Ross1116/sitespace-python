#!/usr/bin/env python3
"""
Comprehensive test script for the complete FastAPI application
"""
import requests
import json
import time

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

def test_authentication():
    """Test authentication endpoints"""
    print("\n🔐 Testing Authentication...")
    
    # Test signup
    try:
        signup_data = {
            "username": "testuser_complete",
            "email": "test_complete@example.com",
            "password": "testpassword123",
            "user_phone": "1234567890",
            "role": "user"
        }
        response = requests.post(f"{BASE_URL}/api/auth/signup", json=signup_data)
        print(f"Signup Status: {response.status_code}")
        print(f"Response: {response.json()}")
        signup_success = response.status_code == 200
    except Exception as e:
        print(f"Signup failed: {e}")
        signup_success = False
    
    # Test signin
    try:
        signin_data = {
            "username": "testuser_complete",
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
    """Test asset endpoints"""
    print("\n🏗️  Testing Asset Management...")
    
    if not token:
        print("No token available for asset tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test asset creation
    try:
        asset_data = {
            "asset_project": "Complete Test Project",
            "asset_title": "Complete Test Asset",
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

def test_slot_booking_endpoints(token):
    """Test slot booking endpoints"""
    print("\n📅 Testing Slot Booking...")
    
    if not token:
        print("No token available for slot booking tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test slot booking creation
    try:
        booking_data = {
            "booking_project": "Test Project",
            "booking_title": "Test Slot Booking",
            "booking_for": "Test User",
            "booked_assets": ["Asset 1", "Asset 2"],
            "booking_status": "pending",
            "booking_time_dt": "2024-01-15T10:00:00",
            "booking_duration_mins": 120,
            "booking_description": "Test booking description",
            "booking_notes": "Test notes"
        }
        response = requests.post(f"{BASE_URL}/api/SlotBooking/saveSlotBooking", json=booking_data, headers=headers)
        print(f"Slot Booking Creation Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Slot booking creation failed: {e}")
        return False

def test_site_project_endpoints(token):
    """Test site project endpoints"""
    print("\n🏢 Testing Site Project...")
    
    if not token:
        print("No token available for site project tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test site project creation
    try:
        project_data = {
            "contractor_key": "test-contractor-key",
            "email_id": "contractor@test.com",
            "contractor_project": ["Project A", "Project B"],
            "contractor_project_id": "PROJ-001",
            "contractor_name": "Test Contractor",
            "contractor_company": "Test Company",
            "contractor_trade": "Electrical",
            "contractor_email": "contractor@test.com",
            "contractor_phone": "9876543210"
        }
        response = requests.post(f"{BASE_URL}/api/SiteProject/saveSiteProject", json=project_data, headers=headers)
        print(f"Site Project Creation Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Site project creation failed: {e}")
        return False

def test_subcontractor_endpoints(token):
    """Test subcontractor endpoints"""
    print("\n👷 Testing Subcontractor...")
    
    if not token:
        print("No token available for subcontractor tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test subcontractor creation
    try:
        subcontractor_data = {
            "name": "Test Subcontractor",
            "email_id": "subcontractor@test.com",
            "contractor_project": ["Project X", "Project Y"],
            "contractor_project_id": "SUB-001",
            "contractor_name": "Test Subcontractor",
            "contractor_company": "Sub Test Company",
            "contractor_trade": "Plumbing",
            "contractor_email": "subcontractor@test.com",
            "contractor_phone": "5555555555",
            "contractor_pass": "testpassword123"
        }
        response = requests.post(f"{BASE_URL}/api/Subcontractor/saveSubcontractor", json=subcontractor_data, headers=headers)
        print(f"Subcontractor Creation Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Subcontractor creation failed: {e}")
        return False

def test_forgot_password():
    """Test forgot password endpoints"""
    print("\n🔑 Testing Forgot Password...")
    
    # Test password reset request
    try:
        reset_request_data = {
            "email": "test_complete@example.com"
        }
        response = requests.post(f"{BASE_URL}/api/forgot-password/request-reset", json=reset_request_data)
        print(f"Password Reset Request Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Password reset request failed: {e}")
        return False

def test_file_upload(token):
    """Test file upload endpoint"""
    print("\n📁 Testing File Upload...")
    
    if not token:
        print("No token available for file upload tests")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Create a test file
    try:
        test_file_content = b"This is a test file content"
        files = {"file": ("test.txt", test_file_content, "text/plain")}
        response = requests.post(f"{BASE_URL}/api/uploadfile", files=files, headers=headers)
        print(f"File Upload Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"File upload failed: {e}")
        return False

def main():
    """Run all comprehensive tests"""
    print("🚀 Testing Complete Sitespace FastAPI Application")
    print("=" * 60)
    
    # Test basic endpoints
    print("\n1. Testing basic endpoints...")
    health_ok = test_health_check()
    
    if not health_ok:
        print("❌ Basic endpoint tests failed. Make sure the server is running.")
        return
    
    print("✅ Basic endpoints working!")
    
    # Test authentication
    print("\n2. Testing authentication...")
    token = test_authentication()
    if not token:
        print("❌ Authentication failed")
        return
    
    print("✅ Authentication working!")
    
    # Test all endpoints
    print("\n3. Testing all endpoints...")
    
    asset_ok = test_asset_endpoints(token)
    slot_booking_ok = test_slot_booking_endpoints(token)
    site_project_ok = test_site_project_endpoints(token)
    subcontractor_ok = test_subcontractor_endpoints(token)
    forgot_password_ok = test_forgot_password()
    file_upload_ok = test_file_upload(token)
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY:")
    print(f"✅ Authentication: {'PASS' if token else 'FAIL'}")
    print(f"✅ Asset Management: {'PASS' if asset_ok else 'FAIL'}")
    print(f"✅ Slot Booking: {'PASS' if slot_booking_ok else 'FAIL'}")
    print(f"✅ Site Project: {'PASS' if site_project_ok else 'FAIL'}")
    print(f"✅ Subcontractor: {'PASS' if subcontractor_ok else 'FAIL'}")
    print(f"✅ Forgot Password: {'PASS' if forgot_password_ok else 'FAIL'}")
    print(f"✅ File Upload: {'PASS' if file_upload_ok else 'FAIL'}")
    
    all_tests_passed = all([
        token, asset_ok, slot_booking_ok, site_project_ok, 
        subcontractor_ok, forgot_password_ok, file_upload_ok
    ])
    
    print("\n" + "=" * 60)
    if all_tests_passed:
        print("🎉 ALL TESTS PASSED! Complete conversion successful!")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
    
    print("=" * 60)

if __name__ == "__main__":
    main()
