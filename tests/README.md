# Sitespace FastAPI Test Suite

This directory contains comprehensive tests for the Sitespace FastAPI application. The tests are designed to be fast, reliable, and easy to run individually or as a complete suite.

## 🚀 Quick Start

### Prerequisites
1. **Start the FastAPI server**:
   ```bash
   uvicorn app.main:app --host localhost --port 8080
   ```

2. **Install dependencies** (if not already installed):
   ```bash
   pip install requests
   ```

### Running Tests

**Run all tests:**
```bash
python tests/run_tests.py
```

**Run specific modules:**
```bash
python tests/run_tests.py auth                    # Authentication tests
python tests/run_tests.py assets                  # Asset management tests  
python tests/run_tests.py booking                 # Slot booking tests
python tests/run_tests.py site_project            # Site project tests
python tests/run_tests.py subcontractor           # Subcontractor tests
python tests/run_tests.py file_upload             # File upload tests
python tests/run_tests.py forgot_password         # Password reset tests
```

**Run multiple modules:**
```bash
python tests/run_tests.py auth assets booking
```

**List available modules:**
```bash
python tests/run_tests.py --list
```

## 📋 Test Modules

### 🔐 Authentication (`test_auth.py`)
Tests user authentication and authorization:
- User signup and signin
- Protected endpoint access
- Token validation
- Unauthorized access blocking
- Invalid credentials handling

**Key scenarios:**
- Manager login and access
- Subcontractor login and access
- Invalid credential rejection

### 🏗️ Asset Management (`test_assets.py`)
Tests asset CRUD operations:
- Asset creation by managers and subcontractors
- Asset listing and filtering
- Asset updates and modifications
- Asset deletion
- Unauthorized access prevention
- Data validation

**Key scenarios:**
- Manager creates new asset
- Asset schema validation
- Access control by role

### 📅 Slot Booking (`test_slot_booking.py`)
Tests booking and scheduling functionality:
- Booking creation by different user types
- Booking list retrieval
- Booking updates (rescheduling)
- Booking deletion
- Unauthorized access prevention

**Key scenarios:**
- Manager books equipment slot
- Subcontractor reschedules booking
- Booking validation and conflict handling

### 🏢 Site Project (`test_site_project.py`)
Tests site project management:
- Project creation and setup
- Project listing and retrieval
- Project updates and modifications
- Project deletion
- Access control

### 👷 Subcontractor (`test_subcontractor.py`)
Tests subcontractor management:
- Subcontractor registration
- Subcontractor listing
- Subcontractor updates
- Access control by role
- Subcontractor-specific permissions

### 📁 File Upload (`test_file_upload.py`)
Tests file upload functionality:
- Successful file uploads
- Different file type handling
- Large file upload limits
- Unauthorized upload prevention
- Empty file handling
- Validation and error handling

### 🔑 Forgot Password (`test_forgot_password.py`)
Tests password reset workflow:
- Password reset request
- Email validation
- Reset token verification
- Password reset completion
- Security validation
- Weak password rejection

## 🛠️ Test Architecture

### Design Principles
- **Real HTTP requests**: Uses `requests` library instead of test clients for reliability
- **Individual modules**: Each API module has its own test file for isolation
- **Realistic scenarios**: Tests cover actual user workflows and edge cases
- **Fast execution**: No complex setup or teardown, just direct API calls
- **Clear output**: Formatted results with emojis and clear pass/fail indicators

### File Structure
```
tests/
├── __init__.py              # Package marker
├── config.py                # Test configuration and constants
├── utils.py                 # Utility functions for testing
├── run_tests.py             # Main test runner script
├── test_auth.py             # Authentication tests
├── test_assets.py           # Asset management tests
├── test_slot_booking.py     # Slot booking tests
├── test_site_project.py     # Site project tests
├── test_subcontractor.py    # Subcontractor tests
├── test_file_upload.py      # File upload tests
├── test_forgot_password.py  # Password reset tests
└── README.md                # This file
```

### Configuration
Test configuration is centralized in `config.py`:
- **BASE_URL**: Server URL (default: http://localhost:8080)
- **TEST_USERS**: Pre-defined test user credentials
- **ENDPOINTS**: API endpoint paths for all modules

### Utilities
Common functionality in `utils.py`:
- **make_request()**: Standardized HTTP request function
- **authenticate_user()**: User authentication helper
- **check_server_health()**: Server availability check
- **print_test_result()**: Formatted test result output

## 🎯 User Scenarios Covered

### Manager Workflows
✅ **Asset Management**:
- Create new assets (excavators, cranes, safety equipment)
- Update asset details and status
- Delete outdated assets
- View asset lists by project

✅ **Booking Management**:
- Create equipment bookings
- Reschedule existing bookings
- View all bookings across projects

✅ **Project Management**:
- Set up new site projects
- Manage project details
- Assign subcontractors

### Subcontractor Workflows  
✅ **Limited Asset Access**:
- View available assets
- Request asset usage (if permitted)

✅ **Booking Operations**:
- Create safety equipment bookings
- Reschedule their own bookings
- View their booking history

✅ **Profile Management**:
- Update contact information
- Manage project assignments

### Security Testing
✅ **Authentication**:
- Valid credential acceptance
- Invalid credential rejection
- Token-based access control

✅ **Authorization**:
- Role-based permissions
- Endpoint access restrictions
- Data isolation between users

✅ **Validation**:
- Input data validation
- File upload restrictions
- Password strength requirements

## 🔧 Troubleshooting

### Common Issues

**Server not running:**
```
❌ Server health check failed!
```
**Solution**: Start the FastAPI server:
```bash
uvicorn app.main:app --host localhost --port 8080
```

**Import errors:**
```
ModuleNotFoundError: No module named 'requests'
```
**Solution**: Install required dependencies:
```bash
pip install requests
```

**Connection refused:**
```
requests.exceptions.ConnectionError
```
**Solution**: Verify server is running on the correct host and port.

### Debug Mode
Run individual test files directly for detailed debugging:
```bash
python tests/test_auth.py           # Run only auth tests
python tests/test_assets.py         # Run only asset tests
```

### Server Wait Mode
If the server takes time to start, use wait mode:
```bash
python tests/run_tests.py --wait
```

## 📊 Sample Output

```
🚀 RUNNING ALL SITESPACE FASTAPI TESTS
============================================================
⏱️  Start time: 2024-12-20 10:30:00

🚀 Running auth tests...
🔐 AUTHENTICATION MODULE TESTS
==================================================
✅ PASS Health Check
✅ PASS User Signup  
✅ PASS User Signin
✅ PASS Protected Endpoints
✅ PASS Unauthorized Access
✅ PASS Invalid Credentials

📊 AUTHENTICATION TEST SUMMARY:
  ✅ PASS Health Check
  ✅ PASS User Signup
  ✅ PASS User Signin
  ✅ PASS Protected Endpoints
  ✅ PASS Unauthorized Access
  ✅ PASS Invalid Credentials

Results: 6/6 tests passed
🎉 All authentication tests passed!

============================================================
📊 OVERALL TEST SUMMARY
============================================================
  ✅ PASS auth           - Authentication and user management
  ✅ PASS assets         - Asset management and CRUD operations
  ✅ PASS booking        - Slot booking and scheduling
  ✅ PASS site_project   - Site project management
  ✅ PASS subcontractor  - Subcontractor management
  ✅ PASS file_upload    - File upload functionality
  ✅ PASS forgot_password - Password reset functionality

📈 RESULTS: 7/7 modules passed
⏱️  Total duration: 45.23 seconds
⏱️  End time: 2024-12-20 10:30:45

🎉 ALL TESTS PASSED! Your Sitespace FastAPI application is working correctly!
```

## 🚀 Next Steps

1. **Run the tests** to verify your API is working correctly
2. **Add new test cases** as you develop new features
3. **Integrate with CI/CD** by running tests in your deployment pipeline
4. **Monitor test results** to catch regressions early

Happy testing! 🧪✨
