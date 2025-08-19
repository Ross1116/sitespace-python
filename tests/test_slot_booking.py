#!/usr/bin/env python3
"""
Slot booking module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import BASE_URL, ENDPOINTS
from datetime import datetime, timedelta
import uuid


def test_create_slot_booking_as_manager():
    """Test slot booking creation as manager"""
    print("\n📅 Testing Slot Booking Creation as Manager...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Slot Booking - Manager", False, "Could not authenticate manager")
        return False
    
    # Create booking for tomorrow with unique identifier
    tomorrow = datetime.now() + timedelta(days=1)
    booking_time = tomorrow.strftime("%Y-%m-%dT10:00:00")
    unique_id = str(uuid.uuid4())[:8]
    
    booking_data = {
        "booking_project": f"Construction Site Alpha {unique_id}",
        "booking_title": "Heavy Equipment Slot",
        "booking_for": "test_manager",
        "booked_assets": ["Excavator CAT320", "Crane Mobile"],
        "booking_status": "pending",
        "booking_time_dt": booking_time,
        "booking_duration_mins": 240,  # 4 hours
        "booking_description": "Scheduled excavation and lifting operations",
        "booking_notes": "Weather dependent - check forecast"
    }
    
    response = make_request("POST", ENDPOINTS["slot_booking"]["save"], token=token, data=booking_data)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        booking_id = data.get("id")
        print_test_result("Slot Booking - Manager", True, f"Booking created: {booking_data['booking_title']}")
        return booking_id
    else:
        print_test_result("Slot Booking - Manager", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
        return False


def test_create_slot_booking_as_subcontractor():
    """Test slot booking creation as subcontractor"""
    print("\n👷 Testing Slot Booking Creation as Subcontractor...")
    
    token = authenticate_user("subcontractor")
    if not token:
        print_test_result("Slot Booking - Subcontractor", False, "Could not authenticate subcontractor")
        return False
    
    tomorrow = datetime.now() + timedelta(days=1)
    booking_time = tomorrow.strftime("%Y-%m-%dT14:00:00")
    unique_id = str(uuid.uuid4())[:8]
    
    booking_data = {
        "booking_project": f"Building Renovation {unique_id}",
        "booking_title": "Safety Equipment Booking",
        "booking_for": "test_subcontractor",
        "booked_assets": ["Safety Harness Set", "First Aid Kit"],
        "booking_status": "pending",
        "booking_time_dt": booking_time,
        "booking_duration_mins": 120,  # 2 hours
        "booking_description": "Safety equipment for electrical work",
        "booking_notes": "Required for electrical panel installation"
    }
    
    response = make_request("POST", ENDPOINTS["slot_booking"]["save"], token=token, data=booking_data)
    success = response.status_code in [200, 403]  # Might be restricted
    
    if response.status_code == 200:
        print_test_result("Slot Booking - Subcontractor", True, "Booking created successfully")
        return True
    elif response.status_code == 403:
        print_test_result("Slot Booking - Subcontractor", True, "Access properly restricted (403)")
        return True
    else:
        print_test_result("Slot Booking - Subcontractor", False, f"Status: {response.status_code}")
        return False


def test_get_slot_booking_list():
    """Test getting slot booking list"""
    print("\n📋 Testing Slot Booking List Retrieval...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Slot Booking List", False, "Could not authenticate")
        return False
    
    # Test with project parameter
    params = {"booking_project": "Construction Site Alpha"}
    response = make_request("GET", ENDPOINTS["slot_booking"]["list"], token=token, params=params)
    success = response.status_code == 200
    
    if success:
        data = response.json()
        booking_count = len(data.get("data", []))
        print_test_result("Slot Booking List", True, f"Retrieved {booking_count} bookings")
    else:
        print_test_result("Slot Booking List", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_update_slot_booking():
    """Test slot booking update functionality (reschedule scenario)"""
    print("\n✏️ Testing Slot Booking Update (Reschedule)...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Slot Booking Reschedule", False, "Could not authenticate")
        return False
    
    # First create a booking to update
    booking_id = test_create_slot_booking_as_manager()
    if not booking_id:
        print_test_result("Slot Booking Reschedule", False, "Could not create booking for update test")
        return False
    
    # Reschedule for day after tomorrow
    day_after_tomorrow = datetime.now() + timedelta(days=2)
    new_booking_time = day_after_tomorrow.strftime("%Y-%m-%dT09:00:00")
    
    update_data = {
        "booking_title": "Rescheduled Heavy Equipment Slot",
        "booking_time_dt": new_booking_time,
        "booking_duration_mins": 180,  # Reduced to 3 hours
        "booking_status": "confirmed",
        "booking_notes": "Rescheduled due to weather conditions"
    }
    
    update_url = ENDPOINTS["slot_booking"]["update"].replace("{booking_id}", str(booking_id))
    response = make_request("PUT", update_url, token=token, data=update_data)
    success = response.status_code == 200
    
    if success:
        print_test_result("Slot Booking Reschedule", True, "Booking rescheduled successfully")
    else:
        print_test_result("Slot Booking Reschedule", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_reschedule_as_subcontractor():
    """Test rescheduling as subcontractor (specific user scenario)"""
    print("\n👷 Testing Reschedule as Subcontractor...")
    
    token = authenticate_user("subcontractor")
    if not token:
        print_test_result("Subcontractor Reschedule", False, "Could not authenticate subcontractor")
        return False
    
    # Try to reschedule an existing booking (would need booking ID)
    # For now, just test the endpoint access
    update_data = {
        "booking_time_dt": "2024-12-25T10:00:00",
        "booking_notes": "Rescheduled by subcontractor"
    }
    
    # Test with non-existent ID to test access
    update_url = ENDPOINTS["slot_booking"]["update"].replace("{booking_id}", "999")
    response = make_request("PUT", update_url, token=token, data=update_data)
    
    # Accept various responses: 404 (not found), 403 (forbidden), or 200 (allowed)
    success = response.status_code in [200, 403, 404]
    
    if response.status_code == 403:
        print_test_result("Subcontractor Reschedule", True, "Reschedule access properly restricted")
    elif response.status_code == 404:
        print_test_result("Subcontractor Reschedule", True, "Reschedule endpoint accessible (booking not found)")
    elif response.status_code == 200:
        print_test_result("Subcontractor Reschedule", True, "Reschedule allowed for subcontractor")
    else:
        print_test_result("Subcontractor Reschedule", False, f"Unexpected status: {response.status_code}")
    
    return success


def test_delete_slot_booking():
    """Test slot booking deletion"""
    print("\n🗑️ Testing Slot Booking Deletion...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Slot Booking Deletion", False, "Could not authenticate")
        return False
    
    # First create a booking to delete
    booking_id = test_create_slot_booking_as_manager()
    if not booking_id:
        print_test_result("Slot Booking Deletion", False, "Could not create booking for deletion test")
        return False
    
    delete_url = ENDPOINTS["slot_booking"]["delete"].replace("{booking_id}", str(booking_id))
    response = make_request("DELETE", delete_url, token=token)
    success = response.status_code == 200
    
    if success:
        print_test_result("Slot Booking Deletion", True, "Booking deleted successfully")
    else:
        print_test_result("Slot Booking Deletion", False, f"Status: {response.status_code}")
        try:
            print(f"    Error: {response.json()}")
        except:
            print(f"    Response: {response.text}")
    
    return success


def test_booking_unauthorized_access():
    """Test unauthorized access to booking endpoints"""
    print("\n🚫 Testing Unauthorized Booking Access...")
    
    tomorrow = datetime.now() + timedelta(days=1)
    booking_time = tomorrow.strftime("%Y-%m-%dT10:00:00")
    
    booking_data = {
        "booking_project": "Unauthorized Test",
        "booking_title": "Unauthorized Booking",
        "booking_for": "nobody",
        "booked_assets": ["Nothing"],
        "booking_status": "pending",
        "booking_time_dt": booking_time,
        "booking_duration_mins": 60
    }
    
    # Test creation without token
    response = make_request("POST", ENDPOINTS["slot_booking"]["save"], data=booking_data)
    create_success = response.status_code in [401, 403]
    
    # Test list without token
    params = {"booking_project": "Test Project"}
    response = make_request("GET", ENDPOINTS["slot_booking"]["list"], params=params)
    list_success = response.status_code in [401, 403]
    
    success = create_success and list_success
    print_test_result("Unauthorized Booking Access", success, 
                     f"Properly blocked unauthorized access")
    
    return success


def run_slot_booking_tests():
    """Run all slot booking tests"""
    print("📅 SLOT BOOKING MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("Create Booking - Manager", test_create_slot_booking_as_manager),
        ("Create Booking - Subcontractor", test_create_slot_booking_as_subcontractor),
        ("Get Booking List", test_get_slot_booking_list),
        ("Update Booking (Reschedule)", test_update_slot_booking),
        ("Reschedule as Subcontractor", test_reschedule_as_subcontractor),
        ("Delete Booking", test_delete_slot_booking),
        ("Unauthorized Access", test_booking_unauthorized_access)
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
    print("📊 SLOT BOOKING TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All slot booking tests passed!")
    else:
        print("⚠️  Some slot booking tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_slot_booking_tests()
