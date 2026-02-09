#!/usr/bin/env python3
"""
Test runner for Sitespace FastAPI application

This script allows you to run individual test modules or all tests.
Usage examples:
    python tests/run_tests.py                    # Run all tests
    python tests/run_tests.py auth               # Run only auth tests
    python tests/run_tests.py assets booking     # Run assets and booking tests
    python tests/run_tests.py --list             # List available test modules
"""
import sys
import os
import argparse
import importlib
import time

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.utils import check_server_health, wait_for_server


# Available test modules
TEST_MODULES = {
    "auth": {
        "module": "tests.test_auth",
        "function": "run_auth_tests",
        "description": "Authentication and user management"
    },
    "assets": {
        "module": "tests.test_assets", 
        "function": "run_asset_tests",
        "description": "Asset management and CRUD operations"
    },
    "booking": {
        "module": "tests.test_slot_booking",
        "function": "run_slot_booking_tests", 
        "description": "Slot booking and scheduling"
    },
    "site_project": {
        "module": "tests.test_site_project",
        "function": "run_site_project_tests",
        "description": "Site project management"
    },
    "subcontractor": {
        "module": "tests.test_subcontractor",
        "function": "run_subcontractor_tests",
        "description": "Subcontractor management"
    },
    "file_upload": {
        "module": "tests.test_file_upload",
        "function": "run_file_upload_tests",
        "description": "File upload functionality"
    }
}


def list_test_modules():
    """List all available test modules"""
    print("📋 AVAILABLE TEST MODULES:")
    print("=" * 60)
    
    for module_name, module_info in TEST_MODULES.items():
        print(f"  🧪 {module_name:<15} - {module_info['description']}")
    
    print("\n💡 USAGE EXAMPLES:")
    print("  python tests/run_tests.py                    # Run all tests")
    print("  python tests/run_tests.py auth               # Run only auth tests")
    print("  python tests/run_tests.py assets booking     # Run multiple modules")
    print("  python tests/run_tests.py --list             # Show this list")


def run_test_module(module_name):
    """
    Run a specific test module
    
    Args:
        module_name: Name of the test module to run
    
    Returns:
        True if tests passed, False otherwise
    """
    if module_name not in TEST_MODULES:
        print(f"❌ Unknown test module: {module_name}")
        print(f"Available modules: {', '.join(TEST_MODULES.keys())}")
        return False
    
    module_info = TEST_MODULES[module_name]
    
    try:
        # Import the test module
        module = importlib.import_module(module_info["module"])
        
        # Get the test function
        test_function = getattr(module, module_info["function"])
        
        # Run the tests
        print(f"\n🚀 Running {module_name} tests...")
        print("⏱️  Start time:", time.strftime("%Y-%m-%d %H:%M:%S"))
        
        start_time = time.time()
        result = test_function()
        end_time = time.time()
        
        duration = end_time - start_time
        print(f"⏱️  Duration: {duration:.2f} seconds")
        
        return result
        
    except ImportError as e:
        print(f"❌ Failed to import test module {module_name}: {e}")
        return False
    except AttributeError as e:
        print(f"❌ Failed to find test function in {module_name}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error running {module_name} tests: {e}")
        return False


def run_all_tests():
    """
    Run all available test modules
    
    Returns:
        Dictionary with results for each module
    """
    print("🚀 RUNNING ALL SITESPACE FASTAPI TESTS")
    print("=" * 60)
    print("⏱️  Start time:", time.strftime("%Y-%m-%d %H:%M:%S"))
    
    overall_start = time.time()
    results = {}
    
    for module_name in TEST_MODULES.keys():
        results[module_name] = run_test_module(module_name)
        print()  # Add spacing between modules
    
    overall_end = time.time()
    overall_duration = overall_end - overall_start
    
    # Summary
    print("=" * 60)
    print("📊 OVERALL TEST SUMMARY")
    print("=" * 60)
    
    passed_modules = []
    failed_modules = []
    
    for module_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        description = TEST_MODULES[module_name]["description"]
        print(f"  {status} {module_name:<15} - {description}")
        
        if success:
            passed_modules.append(module_name)
        else:
            failed_modules.append(module_name)
    
    print(f"\n📈 RESULTS: {len(passed_modules)}/{len(results)} modules passed")
    print(f"⏱️  Total duration: {overall_duration:.2f} seconds")
    print(f"⏱️  End time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if failed_modules:
        print(f"\n❌ Failed modules: {', '.join(failed_modules)}")
    
    if len(passed_modules) == len(results):
        print("\n🎉 ALL TESTS PASSED! Your Sitespace FastAPI application is working correctly!")
    else:
        print(f"\n⚠️  {len(failed_modules)} module(s) failed. Check the output above for details.")
    
    return results


def main():
    """Main function to handle command line arguments and run tests"""
    parser = argparse.ArgumentParser(
        description="Test runner for Sitespace FastAPI application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/run_tests.py                    # Run all tests
  python tests/run_tests.py auth               # Run only auth tests  
  python tests/run_tests.py assets booking     # Run assets and booking tests
  python tests/run_tests.py --list             # List available test modules
  python tests/run_tests.py --wait             # Wait for server before testing
        """
    )
    
    parser.add_argument(
        "modules", 
        nargs="*", 
        choices=list(TEST_MODULES.keys()),
        help="Test modules to run (if none specified, runs all)"
    )
    
    parser.add_argument(
        "--list", 
        action="store_true",
        help="List available test modules"
    )
    
    parser.add_argument(
        "--wait",
        action="store_true", 
        help="Wait for server to be available before running tests"
    )
    
    parser.add_argument(
        "--no-health-check",
        action="store_true",
        help="Skip initial server health check"
    )
    
    args = parser.parse_args()
    
    # Show list of modules if requested
    if args.list:
        list_test_modules()
        return
    
    # Wait for server if requested
    if args.wait:
        print("⏳ Waiting for server to be available...")
        if not wait_for_server():
            print("❌ Server is not available after waiting. Please start the server first.")
            print("   Expected server at: http://localhost:8080")
            sys.exit(1)
        print("✅ Server is available!")
    
    # Check server health (unless skipped)
    if not args.no_health_check:
        print("🏥 Checking server health...")
        if not check_server_health():
            print("❌ Server health check failed!")
            print("   Make sure the FastAPI server is running at http://localhost:8080")
            print("   You can skip this check with --no-health-check flag")
            print("\n💡 To start the server, run:")
            print("   uvicorn app.main:app --host localhost --port 8080")
            sys.exit(1)
        print("✅ Server is healthy!")
    
    # Determine which modules to run
    if args.modules:
        # Run specific modules
        print(f"🎯 Running selected modules: {', '.join(args.modules)}")
        
        overall_success = True
        for module_name in args.modules:
            success = run_test_module(module_name)
            if not success:
                overall_success = False
        
        if overall_success:
            print(f"\n🎉 All selected modules passed!")
        else:
            print(f"\n⚠️  Some selected modules failed.")
            sys.exit(1)
    
    else:
        # Run all modules
        results = run_all_tests()
        
        # Exit with error code if any tests failed
        if not all(results.values()):
            sys.exit(1)


if __name__ == "__main__":
    main()
