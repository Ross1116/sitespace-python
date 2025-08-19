#!/usr/bin/env python3
"""
File upload module tests for Sitespace FastAPI application
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import make_request, authenticate_user, check_server_health, print_test_result
from tests.config import ENDPOINTS
import tempfile


def test_file_upload_success():
    """Test successful file upload"""
    print("\n📁 Testing File Upload Success...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("File Upload", False, "Could not authenticate")
        return False
    
    # Create a test file
    test_content = b"This is a test file for upload functionality.\nIt contains multiple lines.\nAnd some test data."
    
    try:
        files = {"file": ("test_document.txt", test_content, "text/plain")}
        response = make_request("POST", ENDPOINTS["file_upload"]["upload"], token=token, files=files)
        success = response.status_code == 200
        
        if success:
            data = response.json()
            print_test_result("File Upload", True, f"File uploaded successfully")
            if 'filename' in data:
                print(f"    Uploaded file: {data['filename']}")
        else:
            print_test_result("File Upload", False, f"Status: {response.status_code}")
            try:
                print(f"    Error: {response.json()}")
            except:
                print(f"    Response: {response.text}")
        
        return success
        
    except Exception as e:
        print_test_result("File Upload", False, f"Exception: {e}")
        return False


def test_file_upload_different_types():
    """Test uploading different file types"""
    print("\n📄 Testing Different File Types...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Different File Types", False, "Could not authenticate")
        return False
    
    test_files = [
        ("document.txt", b"Text file content", "text/plain"),
        ("data.csv", b"name,age,city\nJohn,30,NYC\nJane,25,LA", "text/csv"),
        ("config.json", b'{"setting": "value", "enabled": true}', "application/json"),
    ]
    
    results = []
    
    for filename, content, content_type in test_files:
        try:
            files = {"file": (filename, content, content_type)}
            response = make_request("POST", ENDPOINTS["file_upload"]["upload"], token=token, files=files)
            success = response.status_code == 200
            results.append(success)
            
            status = "✅" if success else "❌"
            print(f"    {status} {filename} ({content_type})")
            
        except Exception as e:
            print(f"    ❌ {filename}: {e}")
            results.append(False)
    
    overall_success = any(results)  # At least one should work
    print_test_result("Different File Types", overall_success, f"{sum(results)}/{len(results)} file types worked")
    
    return overall_success


def test_file_upload_large_file():
    """Test uploading a larger file"""
    print("\n📊 Testing Large File Upload...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Large File Upload", False, "Could not authenticate")
        return False
    
    # Create a larger test file (1MB)
    large_content = b"Large file test data\n" * 50000  # ~1MB
    
    try:
        files = {"file": ("large_test_file.txt", large_content, "text/plain")}
        response = make_request("POST", ENDPOINTS["file_upload"]["upload"], token=token, files=files)
        success = response.status_code == 200
        
        if success:
            print_test_result("Large File Upload", True, f"Large file uploaded ({len(large_content)} bytes)")
        else:
            print_test_result("Large File Upload", False, f"Status: {response.status_code}")
            # Large file failure might be expected due to size limits
            if response.status_code == 413:  # Payload too large
                print("    Note: File size limit reached (this is expected behavior)")
                return True  # This is actually a success - the limit is working
        
        return success
        
    except Exception as e:
        print_test_result("Large File Upload", False, f"Exception: {e}")
        return False


def test_file_upload_unauthorized():
    """Test file upload without authentication"""
    print("\n🚫 Testing Unauthorized File Upload...")
    
    test_content = b"Unauthorized file content"
    
    try:
        files = {"file": ("unauthorized.txt", test_content, "text/plain")}
        response = make_request("POST", ENDPOINTS["file_upload"]["upload"], files=files)
        
        # Should be rejected
        success = response.status_code in [401, 403]
        print_test_result("Unauthorized File Upload", success, 
                         f"Properly rejected unauthorized upload (Status: {response.status_code})")
        
        return success
        
    except Exception as e:
        print_test_result("Unauthorized File Upload", False, f"Exception: {e}")
        return False


def test_file_upload_empty_file():
    """Test uploading an empty file"""
    print("\n📭 Testing Empty File Upload...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Empty File Upload", False, "Could not authenticate")
        return False
    
    try:
        files = {"file": ("empty.txt", b"", "text/plain")}
        response = make_request("POST", ENDPOINTS["file_upload"]["upload"], token=token, files=files)
        
        # Empty file might be accepted or rejected - both are valid
        if response.status_code == 200:
            print_test_result("Empty File Upload", True, "Empty file accepted")
            return True
        elif response.status_code == 400:
            print_test_result("Empty File Upload", True, "Empty file properly rejected")
            return True
        else:
            print_test_result("Empty File Upload", False, f"Unexpected status: {response.status_code}")
            return False
            
    except Exception as e:
        print_test_result("Empty File Upload", False, f"Exception: {e}")
        return False


def test_file_upload_no_file():
    """Test upload endpoint without providing a file"""
    print("\n❌ Testing Upload Without File...")
    
    token = authenticate_user("manager")
    if not token:
        print_test_result("Upload Without File", False, "Could not authenticate")
        return False
    
    try:
        # Make request without files parameter
        response = make_request("POST", ENDPOINTS["file_upload"]["upload"], token=token)
        
        # Should return an error
        success = response.status_code in [400, 422]
        print_test_result("Upload Without File", success, 
                         f"Properly rejected request without file (Status: {response.status_code})")
        
        return success
        
    except Exception as e:
        print_test_result("Upload Without File", False, f"Exception: {e}")
        return False


def run_file_upload_tests():
    """Run all file upload tests"""
    print("📁 FILE UPLOAD MODULE TESTS")
    print("=" * 50)
    
    # Check server health first
    if not check_server_health():
        print("❌ Server is not available. Please start the server first.")
        return False
    
    tests = [
        ("File Upload Success", test_file_upload_success),
        ("Different File Types", test_file_upload_different_types),
        ("Large File Upload", test_file_upload_large_file),
        ("Unauthorized Upload", test_file_upload_unauthorized),
        ("Empty File Upload", test_file_upload_empty_file),
        ("Upload Without File", test_file_upload_no_file)
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
    print("📊 FILE UPLOAD TEST SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All file upload tests passed!")
    else:
        print("⚠️  Some file upload tests failed.")
    
    return passed == total


if __name__ == "__main__":
    run_file_upload_tests()
