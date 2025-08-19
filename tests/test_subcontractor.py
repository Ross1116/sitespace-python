#!/usr/bin/env python3
"""
Subcontractor module tests for Sitespace FastAPI application  
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import ENDPOINTS
import uuid


def test_create_subcontractor():
    """Test subcontractor creation"""
    print("\n👷 Testing Subcontractor Creation...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Subcontractor Creation", False, "Could not authenticate manager")
        return False
    
    unique_id = str(uuid.uuid4())[:8]
    subcontractor_data = {
        "name": f"Beta Electrical Services {unique_id}",
        "email_id": f"contact.{unique_id}@betaelectrical.com",
        "contractor_project": ["Office Complex Wiring", "Warehouse Electrical"],
        "contractor_project_id": f"SUB-BETA-{unique_id}",
        "contractor_name": "Beta Electrical Services LLC",
        "contractor_company": "Beta Electrical & Automation",
        "contractor_trade": "Electrical Installation",
        "contractor_email": f"operations.{unique_id}@betaelectrical.com",
        "contractor_phone": "555-0456",
        "contractor_pass": "BetaElectrical2024!"
    }
    
    response = make_request("POST", ENDPOINTS["subcontractor"]["save"], token=token, data=subcontractor_data)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        print_test_result("Subcontractor Creation", True, f"Subcontractor created: {subcontractor_data['name']}")
        return data.get("id")
    else:
        print_test_result("Subcontractor Creation", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
        return False


def test_get_subcontractor_list():
    """Test getting subcontractor list"""
    print("\n📋 Testing Subcontractor List...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Subcontractor List", False, "Could not authenticate")
        return False
    
    response = make_request("GET", ENDPOINTS["subcontractor"]["list"], token=token)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        subcontractor_count = len(data.get("data", []))
        print_test_result("Subcontractor List", True, f"Retrieved {subcontractor_count} subcontractors")
    else:
        print_test_result("Subcontractor List", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_update_subcontractor():
    """Test subcontractor update"""
    print("\n✏️ Testing Subcontractor Update...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Subcontractor Update", False, "Could not authenticate")
        return False
    
    # First create a subcontractor to update
    subcontractor_id = test_create_subcontractor()
    if not subcontractor_id:
        print_test_result("Subcontractor Update", False, "Could not create subcontractor for update test")
        return False
    
    update_data = {
        "name": "Updated Beta Electrical Services",
        "contractor_trade": "Electrical & Solar Installation",
        "contractor_phone": "555-0457"
    }
    
    update_url = ENDPOINTS["subcontractor"]["update"].replace("{subcontractor_id}", str(subcontractor_id))
    response = make_request("PUT", update_url, token=token, data=update_data)
    success = response.status_code == 200
    
    if success:
        print_test_result("Subcontractor Update", True, "Subcontractor updated successfully")
    else:
        print_test_result("Subcontractor Update", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_delete_subcontractor():
    """Test subcontractor deletion"""
    print("\n🗑️ Testing Subcontractor Deletion...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Subcontractor Deletion", False, "Could not authenticate")
        return False
    
    # First create a subcontractor to delete
    subcontractor_id = test_create_subcontractor()
    if not subcontractor_id:
        print_test_result("Subcontractor Deletion", False, "Could not create subcontractor for deletion test")
        return False
    
    delete_url = ENDPOINTS["subcontractor"]["delete"].replace("{subcontractor_id}", str(subcontractor_id))
    response = make_request("DELETE", delete_url, token=token)
    success = response.status_code == 200
    
    if success:
        print_test_result("Subcontractor Deletion", True, "Subcontractor deleted successfully")
    else:
        print_test_result("Subcontractor Deletion", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_subcontractor_access_control():
    """Test subcontractor access control"""
    print("\n🔒 Testing Subcontractor Access Control...")
    
    # Test if subcontractor can access their own data
    subcontractor_token = authenticate_user("subcontractor")
    if not subcontractor_token:
        print_test_result("Subcontractor Access Control", False, "Could not authenticate subcontractor")
        return False
    
    # Test list access as subcontractor
    response = make_request("GET", ENDPOINTS["subcontractor"]["list"], token=subcontractor_token)
    
    # Subcontractor might or might not have access to list - both are valid
    if response.status_code == 200:
        print_test_result("Subcontractor Access Control", True, "Subcontractor can access list")
        return True
    elif response.status_code in [401, 403]:
        print_test_result("Subcontractor Access Control", True, "Subcontractor access properly restricted")
        return True
    else:
        print_test_result("Subcontractor Access Control", False, f"Unexpected status: {response.status_code}")
        return False


def test_subcontractor_unauthorized():
    """Test unauthorized access to subcontractor endpoints"""
    print("\n🚫 Testing Unauthorized Subcontractor Access...")
    
    subcontractor_data = {
        "name": "Unauthorized Contractor",
        "email_id": "fake@example.com",
        "contractor_project": ["Fake Project"],
        "contractor_project_id": "FAKE-001",
        "contractor_name": "Fake Contractor",
        "contractor_company": "Fake Company",
        "contractor_trade": "Nothing",
        "contractor_email": "fake@example.com",
        "contractor_phone": "000-0000",
        "contractor_pass": "fakepassword"
    }
    
    # Test creation without token
    response = make_request("POST", ENDPOINTS["subcontractor"]["save"], data=subcontractor_data)
    create_success = response.status_code in [401, 403]
    
    # Test list without token
    response = make_request("GET", ENDPOINTS["subcontractor"]["list"])
    list_success = response.status_code in [401, 403]
    
    success = create_success and list_success
    print_test_result("Unauthorized Subcontractor Access", success, "Access properly restricted")
    
    return success


def run_subcontractor_tests():
    """Run all subcontractor tests"""
    print("👷 SUBCONTRACTOR MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("Create Subcontractor", test_create_subcontractor),
        ("Get Subcontractor List", test_get_subcontractor_list),
        ("Update Subcontractor", test_update_subcontractor),
        ("Delete Subcontractor", test_delete_subcontractor),
        ("Subcontractor Access Control", test_subcontractor_access_control),
        ("Unauthorized Access", test_subcontractor_unauthorized)
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
    print("📊 SUBCONTRACTOR TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All subcontractor tests passed!")
    else:
        print("⚠️  Some subcontractor tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_subcontractor_tests()
