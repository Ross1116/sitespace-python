#!/usr/bin/env python3
"""
Site project module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import ENDPOINTS
import uuid


def test_create_site_project():
    """Test site project creation"""
    print("\n🏢 Testing Site Project Creation...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Site Project Creation", False, "Could not authenticate manager")
        return False
    
    unique_id = str(uuid.uuid4())[:8]
    project_data = {
        "contractor_key": f"PROJ-ALPHA-{unique_id}",
        "email_id": f"project.alpha.{unique_id}@construction.com",
        "contractor_project": ["Highway Extension", "Bridge Construction"],
        "contractor_project_id": f"PROJ-ALPHA-{unique_id}",
        "contractor_name": "Alpha Construction Corp",
        "contractor_company": "Alpha Construction & Engineering Ltd",
        "contractor_trade": "Civil Engineering",
        "contractor_email": f"contact.{unique_id}@alphaconstruction.com",
        "contractor_phone": "555-0123"
    }
    
    response = make_request("POST", ENDPOINTS["site_project"]["save"], token=token, data=project_data)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        print_test_result("Site Project Creation", True, f"Project created: {project_data['contractor_name']}")
        return data.get("id")
    else:
        print_test_result("Site Project Creation", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
        return False


def test_get_site_project_list():
    """Test getting site project list"""
    print("\n📋 Testing Site Project List...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Site Project List", False, "Could not authenticate")
        return False
    
    response = make_request("GET", ENDPOINTS["site_project"]["list"], token=token)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        project_count = len(data.get("data", []))
        print_test_result("Site Project List", True, f"Retrieved {project_count} projects")
    else:
        print_test_result("Site Project List", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_update_site_project():
    """Test site project update"""
    print("\n✏️ Testing Site Project Update...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Site Project Update", False, "Could not authenticate")
        return False
    
    # First create a project to update
    project_id = test_create_site_project()
    if not project_id:
        print_test_result("Site Project Update", False, "Could not create project for update test")
        return False
    
    update_data = {
        "contractor_name": "Updated Alpha Construction Corp",
        "contractor_trade": "Civil & Structural Engineering",
        "contractor_phone": "555-0124"
    }
    
    update_url = ENDPOINTS["site_project"]["update"].replace("{project_id}", str(project_id))
    response = make_request("PUT", update_url, token=token, data=update_data)
    success = response.status_code == 200
    
    if success:
        print_test_result("Site Project Update", True, "Project updated successfully")
    else:
        print_test_result("Site Project Update", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_delete_site_project():
    """Test site project deletion"""
    print("\n🗑️ Testing Site Project Deletion...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Site Project Deletion", False, "Could not authenticate")
        return False
    
    # First create a project to delete
    project_id = test_create_site_project()
    if not project_id:
        print_test_result("Site Project Deletion", False, "Could not create project for deletion test")
        return False
    
    delete_url = ENDPOINTS["site_project"]["delete"].replace("{project_id}", str(project_id))
    response = make_request("DELETE", delete_url, token=token)
    success = response.status_code == 200
    
    if success:
        print_test_result("Site Project Deletion", True, "Project deleted successfully")
    else:
        print_test_result("Site Project Deletion", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_site_project_unauthorized():
    """Test unauthorized access to site project endpoints"""
    print("\n🚫 Testing Unauthorized Site Project Access...")
    
    project_data = {
        "contractor_key": "UNAUTHORIZED",
        "email_id": "fake@example.com",
        "contractor_project": ["Fake Project"],
        "contractor_project_id": "FAKE-001",
        "contractor_name": "Fake Contractor",
        "contractor_company": "Fake Company",
        "contractor_trade": "Nothing",
        "contractor_email": "fake@example.com",
        "contractor_phone": "000-0000"
    }
    
    # Test creation without token
    response = make_request("POST", ENDPOINTS["site_project"]["save"], data=project_data)
    create_success = response.status_code in [401, 403]
    
    # Test list without token
    response = make_request("GET", ENDPOINTS["site_project"]["list"])
    list_success = response.status_code in [401, 403]
    
    success = create_success and list_success
    print_test_result("Unauthorized Site Project Access", success, "Access properly restricted")
    
    return success


def run_site_project_tests():
    """Run all site project tests"""
    print("🏢 SITE PROJECT MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("Create Site Project", test_create_site_project),
        ("Get Project List", test_get_site_project_list),
        ("Update Site Project", test_update_site_project),
        ("Delete Site Project", test_delete_site_project),
        ("Unauthorized Access", test_site_project_unauthorized)
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
    print("📊 SITE PROJECT TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All site project tests passed!")
    else:
        print("⚠️  Some site project tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_site_project_tests()
