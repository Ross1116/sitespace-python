#!/usr/bin/env python3
"""
Asset management module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import BASE_URL, ENDPOINTS
import uuid


def test_create_asset_as_manager():
    """Test asset creation as manager"""
    print("\n🏗️ Testing Asset Creation as Manager...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Asset Creation - Manager", False, "Could not authenticate manager")
        return False
    
    unique_id = str(uuid.uuid4())[:8]
    asset_data = {
        "asset_project": "Test Construction Project",
        "asset_title": "Heavy Excavator CAT320",
        "asset_location": "Site A - Zone 1", 
        "asset_status": "active",
        "asset_poc": "test_manager",
        "usage_instructions": "Large excavator for earth moving operations",
        "asset_key": f"EXC_CAT320_{unique_id}"
    }
    
    response = make_request("POST", ENDPOINTS["assets"]["save"], token=token, data=asset_data)
    success = response.status_code == 200
    
    if success:
        try:
            data = response.json()
            print_test_result("Asset Creation - Manager", True, f"Asset created: {asset_data['asset_title']}")
            # Return asset ID directly from response
            if isinstance(data, dict) and "id" in data:
                asset_id = data["id"]
                return asset_id
            else:
                return True  # Creation successful but no ID returned
        except Exception as e:
            print_test_result("Asset Creation - Manager", False, f"Response parsing error: {e}")
            return False
    else:
        print_test_result("Asset Creation - Manager", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
        return False


def test_create_asset_as_subcontractor():
    """Test asset creation as subcontractor"""
    print("\n👷 Testing Asset Creation as Subcontractor...")
    
    token = authenticate_user("subcontractor")
    if not token:
        print_test_result("Asset Creation - Subcontractor", False, "Could not authenticate subcontractor")
        return False
    
    unique_id = str(uuid.uuid4())[:8]
    asset_data = {
        "asset_project": "Building Renovation",
        "asset_title": "Safety Equipment Set",
        "asset_location": "Site B - Safety Station",
        "asset_status": "active", 
        "asset_poc": "test_subcontractor",
        "usage_instructions": "Complete safety equipment for workers",
        "asset_key": f"SAFE_SET_{unique_id}"
    }
    
    response = make_request("POST", ENDPOINTS["assets"]["save"], token=token, data=asset_data)
    success = response.status_code in [200, 403]  # Might be restricted
    
    if response.status_code == 200:
        print_test_result("Asset Creation - Subcontractor", True, "Asset created successfully")
        return True
    elif response.status_code == 403:
        print_test_result("Asset Creation - Subcontractor", True, "Access properly restricted (403)")
        return True
    else:
        print_test_result("Asset Creation - Subcontractor", False, f"Status: {response.status_code}")
        return False


def test_get_asset_list():
    """Test getting asset list"""
    print("\n📋 Testing Asset List Retrieval...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Asset List", False, "Could not authenticate")
        return False
    
    # Test with project parameter
    params = {"asset_project": "Test Construction Project"}
    response = make_request("GET", ENDPOINTS["assets"]["list"], token=token, params=params)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        asset_count = len(data.get("data", []))
        print_test_result("Asset List", True, f"Retrieved {asset_count} assets")
    else:
        print_test_result("Asset List", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_update_asset():
    """Test asset update functionality"""
    print("\n✏️ Testing Asset Update...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Asset Update", False, "Could not authenticate")
        return False
    
    # First create an asset to update
    asset_id = test_create_asset_as_manager()
    if not asset_id or asset_id is True:
        print_test_result("Asset Update", False, "Could not create asset for update test or no ID returned")
        return False
    
    update_data = {
        "asset_title": "Updated Heavy Excavator",
        "asset_location": "Site A - Zone 2",
        "asset_status": "maintenance",
        "usage_instructions": "Updated description for maintenance"
    }
    
    params = {"asset_id": asset_id}
    response = make_request("POST", ENDPOINTS["assets"]["update"], token=token, data=update_data, params=params)
    success = response.status_code == 200
    
    if success:
        print_test_result("Asset Update", True, "Asset updated successfully")
    else:
        print_test_result("Asset Update", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_delete_asset():
    """Test asset deletion"""
    print("\n🗑️ Testing Asset Deletion...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Asset Deletion", False, "Could not authenticate")
        return False
    
    # First create an asset to delete
    asset_id = test_create_asset_as_manager()
    if not asset_id or asset_id is True:
        print_test_result("Asset Deletion", False, "Could not create asset for deletion test or no ID returned")
        return False
    
    response = make_request("DELETE", f"{ENDPOINTS['assets']['delete']}/{asset_id}", token=token)
    success = response.status_code == 200
    
    if success:
        print_test_result("Asset Deletion", True, "Asset deleted successfully")
    else:
        print_test_result("Asset Deletion", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_asset_unauthorized_access():
    """Test unauthorized access to asset endpoints"""
    print("\n🚫 Testing Unauthorized Asset Access...")
    
    asset_data = {
        "asset_project": "Unauthorized Test",
        "asset_title": "Unauthorized Asset",
        "asset_location": "Nowhere",
        "asset_status": "active"
    }
    
    # Test creation without token
    response = make_request("POST", ENDPOINTS["assets"]["save"], data=asset_data)
    create_success = response.status_code in [401, 403]
    
    # Test list without token
    params = {"asset_project": "Test Project"}
    response = make_request("GET", ENDPOINTS["assets"]["list"], params=params)
    list_success = response.status_code in [401, 403]
    
    success = create_success and list_success
    print_test_result("Unauthorized Asset Access", success, 
                     f"Create: {response.status_code}, List: {response.status_code}")
    
    return success


def test_asset_validation():
    """Test asset validation with invalid data"""
    print("\n✅ Testing Asset Validation...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Asset Validation", False, "Could not authenticate")
        return False
    
    # Test with missing required fields
    invalid_data = {
        "asset_title": "Invalid Asset"
        # Missing required fields like asset_project
    }
    
    response = make_request("POST", ENDPOINTS["assets"]["save"], token=token, data=invalid_data)
    success = response.status_code == 422  # Validation error
    
    print_test_result("Asset Validation", success, 
                     f"Validation properly rejected invalid data (Status: {response.status_code})")
    
    return success


def run_asset_tests():
    """Run all asset management tests"""
    print("🏗️ ASSET MANAGEMENT MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("Create Asset - Manager", test_create_asset_as_manager),
        ("Create Asset - Subcontractor", test_create_asset_as_subcontractor),
        ("Get Asset List", test_get_asset_list),
        ("Update Asset", test_update_asset),
        ("Delete Asset", test_delete_asset),
        ("Unauthorized Access", test_asset_unauthorized_access),
        ("Asset Validation", test_asset_validation)
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
    print("📊 ASSET MANAGEMENT TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All asset management tests passed!")
    else:
        print("⚠️  Some asset management tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_asset_tests()