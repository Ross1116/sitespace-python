"""
Utility functions for tests
"""
import requests
import time
from typing import Optional, Dict, Any
from .config import BASE_URL, TEST_USERS, ENDPOINTS


def make_request(method: str, endpoint: str, token: Optional[str] = None, 
                data: Optional[Dict[Any, Any]] = None, files: Optional[Dict] = None,
                params: Optional[Dict[str, Any]] = None) -> requests.Response:
    """
    Make a request to the API with optional authentication
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        endpoint: API endpoint path
        token: Optional Bearer token for authentication
        data: Optional JSON data for request body
        files: Optional files for upload
        params: Optional query parameters
    
    Returns:
        Response object
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {}
    
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    if method.upper() == "GET":
        return requests.get(url, headers=headers, params=params)
    elif method.upper() == "POST":
        if files:
            return requests.post(url, headers=headers, files=files, params=params)
        else:
            return requests.post(url, headers=headers, json=data, params=params)
    elif method.upper() == "PUT":
        return requests.put(url, headers=headers, json=data, params=params)
    elif method.upper() == "DELETE":
        return requests.delete(url, headers=headers, params=params)
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")


def authenticate_user(user_type: str) -> Optional[str]:
    """
    Authenticate a user and return the access token
    
    Args:
        user_type: Type of user (manager, subcontractor, user)
    
    Returns:
        Access token string or None if authentication failed
    """
    if user_type not in TEST_USERS:
        print(f"❌ Unknown user type: {user_type}")
        return None
    
    user_data = TEST_USERS[user_type].copy()
    
    # First try to sign up (might fail if user already exists)
    signup_response = make_request("POST", ENDPOINTS["auth"]["signup"], data=user_data)
    
    # Then sign in
    signin_data = {
        "username": user_data["username"],
        "password": user_data["password"]
    }
    
    signin_response = make_request("POST", ENDPOINTS["auth"]["signin"], data=signin_data)
    
    if signin_response.status_code == 200:
        token_data = signin_response.json()
        return token_data.get("access_token")
    else:
        print(f"❌ Authentication failed for {user_type}: {signin_response.status_code}")
        if signin_response.status_code != 500:  # Don't print 500 errors as they're usually expected
            try:
                print(f"Response: {signin_response.json()}")
            except:
                print(f"Response text: {signin_response.text}")
        return None


def check_server_health() -> bool:
    """
    Check if the server is running and healthy
    
    Returns:
        True if server is healthy, False otherwise
    """
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Server health check failed: {e}")
        return False


def print_test_result(test_name: str, success: bool, details: str = ""):
    """
    Print formatted test result
    
    Args:
        test_name: Name of the test
        success: Whether the test passed
        details: Optional additional details
    """
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status} {test_name}")
    if details:
        print(f"    {details}")


def wait_for_server(max_retries: int = 10, delay: float = 1.0) -> bool:
    """
    Wait for server to be available
    
    Args:
        max_retries: Maximum number of retries
        delay: Delay between retries in seconds
    
    Returns:
        True if server becomes available, False otherwise
    """
    for i in range(max_retries):
        if check_server_health():
            return True
        print(f"Waiting for server... ({i+1}/{max_retries})")
        time.sleep(delay)
    
    return False
