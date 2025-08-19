#!/usr/bin/env python3
"""
Authentication module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import BASE_URL, TEST_USERS, ENDPOINTS


def test_health_check():
    """Test server health check"""
    print("\n🏥 Testing Server Health...")
    
    success = check_server_health()
    print_test_result("Server Health Check", success)
    
    if success:
        try:
            response = make_request("GET", "/health")
            print(f"    Response: {response.json()}")
        except Exception as e:
            print(f"    Could not parse response: {e}")
    
    return success


def test_user_signup():
    """Test user signup functionality"""
    print("\n📝 Testing User Signup...")
    
    # Test signup for manager
    manager_data = TEST_USERS["manager"].copy()
    manager_data["username"] = f"test_manager_{int(__import__('time').time())}"
    manager_data["email"] = f"manager_{int(__import__('time').time())}@test.com"
    
    response = make_request("POST", ENDPOINTS["auth"]["signup"], data=manager_data)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        print_test_result("Manager Signup", success, f"User created: {data.get('username')}")
    else:
        print_test_result("Manager Signup", success, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_user_signin():
    """Test user signin functionality"""
    print("\n🔐 Testing User Signin...")
    
    # Test signin for each user type
    results = {}
    
    for user_type in ["manager", "subcontractor", "user"]:
        print(f"  Testing {user_type} signin...")
        token = authenticate_user(user_type)
        results[user_type] = token is not None
        
        if token:
            print_test_result(f"{user_type.title()} Signin", True, f"Token: {token[:20]}...")
        else:
            print_test_result(f"{user_type.title()} Signin", False, "Authentication failed")
    
    return any(results.values())


def test_protected_endpoints():
    """Test protected endpoints with authentication"""
    print("\n🔒 Testing Protected Endpoints...")
    
    # Get token for manager
    token = authenticate_user("manager")
    
    if not token:
        print_test_result("Protected Endpoints", False, "Could not get authentication token")
        return False
    
    # Test current user endpoint
    response = make_request("GET", ENDPOINTS["auth"]["current_user"], token=token)
    current_user_success = response.status_code == 200
    
    if current_user_success:
        data = response.json()
        print_test_result("Get Current User", True, f"User: {data.get('username', 'Unknown')}")
    else:
        print_test_result("Get Current User", False, f"Status: {response.status_code}")
    
    # Test get user by token endpoint (expects token as query parameter)
    response = make_request("GET", f"{ENDPOINTS['auth']['user_by_token']}?token={token}")
    user_by_token_success = response.status_code == 200
    
    if user_by_token_success:
        data = response.json()
        print_test_result("Get User by Token", True, f"User: {data.get('username', 'Unknown')}")
    else:
        print_test_result("Get User by Token", False, f"Status: {response.status_code}")
    
    return current_user_success and user_by_token_success


def test_unauthorized_access():
    """Test unauthorized access to protected endpoints"""
    print("\n🚫 Testing Unauthorized Access...")
    
    # Test current user endpoint without token
    response = make_request("GET", ENDPOINTS["auth"]["current_user"])
    unauthorized_success = response.status_code in [401, 403]
    
    print_test_result("Unauthorized Access Blocked", unauthorized_success, 
                     f"Status: {response.status_code} (expected 401/403)")
    
    return unauthorized_success


def test_invalid_credentials():
    """Test signin with invalid credentials"""
    print("\n❌ Testing Invalid Credentials...")
    
    invalid_data = {
        "username": "nonexistent_user",
        "password": "wrongpassword"
    }
    
    response = make_request("POST", ENDPOINTS["auth"]["signin"], data=invalid_data)
    success = response.status_code in [401, 422]
    
    print_test_result("Invalid Credentials Rejected", success, 
                     f"Status: {response.status_code} (expected 401/422)")
    
    return success


def run_auth_tests():
    """Run all authentication tests"""
    print("🔐 AUTHENTICATION MODULE TESTS")
    print("=" * 50)
    
    tests = [
        ("Health Check", test_health_check),
        ("User Signup", test_user_signup),
        ("User Signin", test_user_signin), 
        ("Protected Endpoints", test_protected_endpoints),
        ("Unauthorized Access", test_unauthorized_access),
        ("Invalid Credentials", test_invalid_credentials)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"❌ FAIL {test_name}: {e}")
            results[test_name] = False
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 AUTHENTICATION TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All authentication tests passed!")
    else:
        print("⚠️  Some authentication tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_auth_tests()
