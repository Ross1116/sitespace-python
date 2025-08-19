"""
Test configuration for Sitespace FastAPI application
"""

# Base URL for the API
BASE_URL = "http://localhost:8080"

# Test user credentials
TEST_USERS = {
    "manager": {
        "username": "test_manager",
        "email": "manager@test.com", 
        "password": "testpassword123",
        "user_phone": "1234567890",
        "role": "manager"
    },
    "subcontractor": {
        "username": "test_subcontractor", 
        "email": "subcontractor@test.com",
        "password": "testpassword123",
        "user_phone": "9876543210",
        "role": "subcontractor"
    },
    "user": {
        "username": "test_user",
        "email": "user@test.com",
        "password": "testpassword123", 
        "user_phone": "5555555555",
        "role": "user"
    }
}

# API endpoints
ENDPOINTS = {
    "auth": {
        "signup": "/api/auth/signup",
        "signin": "/api/auth/signin",
        "current_user": "/api/auth/me",
        "user_by_token": "/api/auth/userbytoken"
    },
    "assets": {
        "save": "/api/Asset/saveAsset",
        "list": "/api/Asset/getAssetList",
        "update": "/api/Asset/updateAsset",
        "delete": "/api/Asset/deleteAsset",
        "details": "/api/Asset/editAssetdetails"
    },
    "slot_booking": {
        "save": "/api/SlotBooking/saveSlotBooking",
        "list": "/api/SlotBooking/getSlotBookingList",
        "update": "/api/SlotBooking/updateSlotBooking/{booking_id}",
        "delete": "/api/SlotBooking/deleteSlotBooking/{booking_id}"
    },
    "site_project": {
        "save": "/api/SiteProject/saveSiteProject",
        "list": "/api/SiteProject/getSiteProjectList",
        "update": "/api/SiteProject/updateSiteProject/{project_id}",
        "delete": "/api/SiteProject/deleteSiteProject/{project_id}"
    },
    "subcontractor": {
        "save": "/api/Subcontractor/saveSubcontractor",
        "list": "/api/Subcontractor/getSubcontractorList",
        "update": "/api/Subcontractor/updateSubcontractor/{subcontractor_id}",
        "delete": "/api/Subcontractor/deleteSubcontractor/{subcontractor_id}"
    },
    "file_upload": {
        "upload": "/api/uploadfile"
    },
    "forgot_password": {
        "request_reset": "/api/forgot-password/request-reset",
        "verify_reset": "/api/forgot-password/verify-reset",
        "reset_password": "/api/forgot-password/reset-password"
    }
}
