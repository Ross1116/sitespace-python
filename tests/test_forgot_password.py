#!/usr/bin/env python3
"""
Forgot password module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import ENDPOINTS, TEST_USERS


def test_password_reset_request():
    """Test password reset request"""
    print("\n🔑 Testing Password Reset Request...")
    
    # Use a test user email
    reset_data = {
        "email": TEST_USERS["manager"]["email"]
    }
    
    response = make_request("POST", ENDPOINTS["forgot_password"]["request_reset"], data=reset_data)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        print_test_result("Password Reset Request", True, "Reset request sent successfully")
        if 'message' in data:
            print(f"    Message: {data['message']}")
    else:
        print_test_result("Password Reset Request", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_password_reset_invalid_email():
    """Test password reset with invalid email"""
    print("\n❌ Testing Password Reset with Invalid Email...")
    
    reset_data = {
        "email": "nonexistent@invalid.com"
    }
    
    response = make_request("POST", ENDPOINTS["forgot_password"]["request_reset"], data=reset_data)
    
    # Should either succeed (for security) or return 404/400
    if response.status_code == 200:
        print_test_result("Invalid Email Reset", True, "Request handled securely (generic success)")
        return True
    elif response.status_code in [400, 404]:
        print_test_result("Invalid Email Reset", True, f"Invalid email properly rejected (Status: {response.status_code})")
        return True
    else:
        print_test_result("Invalid Email Reset", False, f"Unexpected status: {response.status_code}")
        return False


def test_password_reset_missing_email():
    """Test password reset without email"""
    print("\n📧 Testing Password Reset Without Email...")
    
    # Send request without email
    reset_data = {}
    
    response = make_request("POST", ENDPOINTS["forgot_password"]["request_reset"], data=reset_data)
    success = response.status_code in [400, 422]  # Validation error expected
    
    print_test_result("Missing Email Reset", success, 
                     f"Properly rejected request without email (Status: {response.status_code})")
    
    return success


def test_password_reset_malformed_email():
    """Test password reset with malformed email"""
    print("\n📧 Testing Password Reset with Malformed Email...")
    
    malformed_emails = [
        "not-an-email",
        "@invalid.com",
        "user@",
        "user@invalid",
        ""
    ]
    
    results = []
    
    for email in malformed_emails:
        reset_data = {"email": email}
        response = make_request("POST", ENDPOINTS["forgot_password"]["request_reset"], data=reset_data)
        
        # Should reject malformed emails
        success = response.status_code in [400, 422]
        results.append(success)
        
        status = "✅" if success else "❌"
        print(f"    {status} '{email}' -> {response.status_code}")
    
    overall_success = all(results)
    print_test_result("Malformed Email Reset", overall_success, 
                     f"{sum(results)}/{len(results)} malformed emails properly rejected")
    
    return overall_success


def test_password_verify_reset():
    """Test password reset verification"""
    print("\n🔍 Testing Password Reset Verification...")
    
    # This would typically require a valid reset token
    # For testing, we'll use a fake token to test the endpoint
    verify_data = {
        "token": "fake-reset-token-for-testing",
        "email": TEST_USERS["manager"]["email"]
    }
    
    response = make_request("POST", ENDPOINTS["forgot_password"]["verify_reset"], data=verify_data)
    
    # Should reject fake token
    if response.status_code in [400, 401, 404]:
        print_test_result("Password Reset Verification", True, 
                         f"Fake token properly rejected (Status: {response.status_code})")
        return True
    elif response.status_code == 200:
        print_test_result("Password Reset Verification", False, 
                         "Fake token was accepted (security issue)")
        return False
    else:
        print_test_result("Password Reset Verification", False, 
                         f"Unexpected status: {response.status_code}")
        return False


def test_password_reset_completion():
    """Test password reset completion"""
    print("\n🔄 Testing Password Reset Completion...")
    
    # This would typically require a valid reset token
    # For testing, we'll use a fake token to test the endpoint
    reset_data = {
        "token": "fake-reset-token-for-testing",
        "new_password": "newpassword123",
        "email": TEST_USERS["manager"]["email"]
    }
    
    response = make_request("POST", ENDPOINTS["forgot_password"]["reset_password"], data=reset_data)
    
    # Should reject fake token
    if response.status_code in [400, 401, 404]:
        print_test_result("Password Reset Completion", True, 
                         f"Fake token properly rejected (Status: {response.status_code})")
        return True
    elif response.status_code == 200:
        print_test_result("Password Reset Completion", False, 
                         "Fake token was accepted (security issue)")
        return False
    else:
        print_test_result("Password Reset Completion", False, 
                         f"Unexpected status: {response.status_code}")
        return False


def test_password_reset_weak_password():
    """Test password reset with weak password"""
    print("\n🔒 Testing Password Reset with Weak Password...")
    
    weak_passwords = [
        "",
        "123",
        "abc",
        "password",
        "12345678"
    ]
    
    results = []
    
    for password in weak_passwords:
        reset_data = {
            "token": "fake-token",
            "new_password": password,
            "email": TEST_USERS["manager"]["email"]
        }
        
        response = make_request("POST", ENDPOINTS["forgot_password"]["reset_password"], data=reset_data)
        
        # Should reject weak passwords (400/422) or fake token (401/404)
        success = response.status_code in [400, 401, 404, 422]
        results.append(success)
        
        status = "✅" if success else "❌"
        print(f"    {status} '{password}' -> {response.status_code}")
    
    overall_success = all(results)
    print_test_result("Weak Password Reset", overall_success, 
                     f"{sum(results)}/{len(results)} weak passwords properly handled")
    
    return overall_success


def run_forgot_password_tests():
    """Run all forgot password tests"""
    print("🔑 FORGOT PASSWORD MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("Password Reset Request", test_password_reset_request),
        ("Invalid Email Reset", test_password_reset_invalid_email),
        ("Missing Email Reset", test_password_reset_missing_email),
        ("Malformed Email Reset", test_password_reset_malformed_email),
        ("Password Reset Verification", test_password_verify_reset),
        ("Password Reset Completion", test_password_reset_completion),
        ("Weak Password Reset", test_password_reset_weak_password)
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
    print("📊 FORGOT PASSWORD TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All forgot password tests passed!")
    else:
        print("⚠️  Some forgot password tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_forgot_password_tests()
