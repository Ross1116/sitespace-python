from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from typing import Optional
from ...core.database import get_db
from ...core.security import create_access_token, get_current_user, get_current_active_user
from ...crud.user import authenticate_user, create_user, get_user_by_username, get_user_by_email
from ...models.user import User
from ...schemas.user import UserLogin, UserSignup, UserCreate, TokenResponse, UserResponse, MessageResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/signin", response_model=TokenResponse)
def authenticate_user_endpoint(
    user_credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Authenticate user and return JWT token
    """
    user = authenticate_user(db, user_credentials.username, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    
    access_token = create_access_token(data={"sub": user.username})
    
    return TokenResponse(
        access_token=access_token,
        user_id=user.user_id or str(user.id),
        username=user.username,
        email=user.email,
        role=user.role
    )

@router.post("/signup", response_model=MessageResponse)
def register_user(
    user_signup: UserSignup,
    db: Session = Depends(get_db)
):
    """
    Register a new user
    """
    # Check if username already exists
    if get_user_by_username(db, username=user_signup.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    if get_user_by_email(db, email=user_signup.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    user_create = UserCreate(
        username=user_signup.username,
        email=user_signup.email,
        password=user_signup.password,
        user_phone=user_signup.user_phone,
        role=user_signup.role,
        user_id=None  # Will be generated
    )
    user = create_user(db, user_create)
    
    return MessageResponse(message="User registered successfully!")

@router.get("/userbytoken", response_model=Optional[UserResponse])
def get_user_by_token(
    token: str,
    db: Session = Depends(get_db)
):
    """
    Get user details by JWT token
    """
    from ...core.security import verify_token
    
    if not token:
        return None
    
    username = verify_token(token)
    if not username:
        return None
    
    user = get_user_by_username(db, username=username)
    if not user:
        return None
    
    # Don't return password
    user.password = None
    return user

@router.post("/signout", response_model=MessageResponse)
def logout_user():
    """
    Logout user (client should remove token)
    """
    return MessageResponse(message="User signed out successfully!")

@router.get("/me", response_model=UserResponse)
def get_current_user_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current user information
    """
    return current_user
