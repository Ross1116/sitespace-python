from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from typing import Optional, Union, Dict, Any
from uuid import UUID

# Import all dependencies at the top
from ...core.database import get_db
from ...core.security import (
    create_access_token,
    create_refresh_token,
    verify_token,
    verify_refresh_token,
    get_current_user,
    get_password_hash,
    verify_password,
    create_verification_token,
    verify_email_token,
    create_password_reset_token,
    verify_password_reset_token
)
from ...core.config import settings
from ...core.email import send_verification_email, send_password_reset_email

# Import models and crud operations
from ...models.user import User
from ...models.subcontractor import Subcontractor
from ...crud import user as user_crud
from ...crud import subcontractor as subcontractor_crud

# Import schemas
from ...schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
    ResendVerificationRequest,
    ResendVerificationResponse
)
from ...schemas.user import UserCreate, UserResponse
from ...schemas.enums import UserRole

router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer()


# ==================== Helper Functions ====================

def get_entity_by_email(db: Session, email: str) -> Union[User, Subcontractor, None]:
    """Get user or subcontractor by email"""
    user = user_crud.get_user_by_email(db, email=email)
    if user:
        return user
    return subcontractor_crud.get_subcontractor_by_email(db, email=email)


def get_entity_by_id(db: Session, entity_id: UUID, user_type: str) -> Union[User, Subcontractor, None]:
    """Get user or subcontractor by ID and type"""
    if user_type == "subcontractor":
        return subcontractor_crud.get_subcontractor(db, user_id=entity_id)
    return user_crud.get_user(db, user_id=entity_id)


def create_token_payload(entity: Union[User, Subcontractor]) -> Dict[str, Any]:
    """Create consistent token payload for user or subcontractor"""
    is_subcontractor = isinstance(entity, Subcontractor)
    user_type = "subcontractor" if is_subcontractor else "user"
    role = "subcontractor" if is_subcontractor else entity.role
    
    return {
        "sub": str(entity.id),
        "email": entity.email,
        "role": role,
        "user_type": user_type
    }


def get_entity_password_hash(entity: Union[User, Subcontractor]) -> str:
    """Get password hash field from entity (handles different field names)"""
    if isinstance(entity, Subcontractor):
        return entity.password_hash
    return entity.password


def update_entity_password(db: Session, entity: Union[User, Subcontractor], new_password: str) -> None:
    """Update password for user or subcontractor"""
    if isinstance(entity, Subcontractor):
        subcontractor_crud.update_password(db, entity, new_password)
    else:
        user_crud.update_password(db, entity, new_password)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets security requirements
    Returns: (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    
    # Optional: Check for special characters
    # special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    # if not any(c in special_chars for c in password):
    #     return False, "Password must contain at least one special character"
    
    return True, ""


def build_token_response(entity: Union[User, Subcontractor]) -> TokenResponse:
    """Build token response for authenticated entity"""
    payload = create_token_payload(entity)
    access_token = create_access_token(data=payload)
    refresh_payload = {"sub": payload["sub"], "user_type": payload["user_type"]}
    refresh_token = create_refresh_token(data=refresh_payload)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=entity.id,
        role=payload["role"]
    )


# ==================== API Endpoints ====================

@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    db: Session = Depends(get_db)
) -> TokenResponse:
    """Authenticate user or subcontractor with email and password"""
    
    entity = get_entity_by_email(db, request.email)
    
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    # Verify password
    password_hash = get_entity_password_hash(entity)
    if not verify_password(request.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    # Check if account is active
    if not entity.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active"
        )
    
    return build_token_response(entity)


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> UserResponse:
    """Register a new user account"""
    
    # Validate password strength
    is_valid, error_message = validate_password_strength(user_data.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )
    
    try:
        # Create new user (let database handle uniqueness constraint)
        user = user_crud.create_user(db, user_data)
        
        # Generate verification token
        verification_token = create_verification_token(user.email)
        
        # Send verification email in background
        background_tasks.add_task(
            send_verification_email,
            user.email,
            f"{user.first_name} {user.last_name}",
            verification_token
        )
        
        return user
        
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    request: RefreshTokenRequest,
    db: Session = Depends(get_db)
) -> TokenResponse:
    """Refresh access token using refresh token"""
    
    try:
        payload = verify_refresh_token(request.refresh_token)
        entity_id = payload.get("sub")
        user_type = payload.get("user_type", "user")
        
        if not entity_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )
        
        entity = get_entity_by_id(db, entity_id, user_type)
        
        if not entity or not entity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account not found or inactive"
            )
        
        return build_token_response(entity)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> ForgotPasswordResponse:
    """Request password reset email for user or subcontractor"""
    
    # Get entity but don't reveal if it exists
    entity = get_entity_by_email(db, request.email)
    
    # Always return success for security (don't reveal if email exists)
    response = ForgotPasswordResponse(
        message="If the email exists, password reset instructions have been sent",
        email=request.email,
        success=True,
        reset_token_sent=bool(entity),
        expires_in_minutes=settings.PASSWORD_RESET_EXPIRE_HOURS * 60
    )
    
    if entity:
        reset_token = create_password_reset_token(entity.email)
        
        # Determine name for email
        if isinstance(entity, Subcontractor) and entity.company_name:
            name = entity.company_name
        else:
            name = f"{entity.first_name} {entity.last_name}"
        
        background_tasks.add_task(
            send_password_reset_email,
            entity.email,
            name,
            reset_token
        )
    
    return response


@router.post("/reset-password", response_model=ResetPasswordResponse)
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db)
) -> ResetPasswordResponse:
    """Reset password for user or subcontractor using reset token"""
    
    # Validate password strength
    is_valid, error_message = validate_password_strength(request.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )
    
    # Verify reset token and get email
    email = verify_password_reset_token(request.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )
    
    # Get entity and update password
    entity = get_entity_by_email(db, email=email)
    
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found"
        )
    
    update_entity_password(db, entity, request.password)
    
    return ResetPasswordResponse(
        message="Password has been reset successfully",
        success=True,
        email=entity.email
    )


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    request: ChangePasswordRequest,
    current_entity: Union[User, Subcontractor] = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChangePasswordResponse:
    """Change password for authenticated user or subcontractor"""
    
    # Validate new password strength
    is_valid, error_message = validate_password_strength(request.new_password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )
    
    # Get current password hash
    current_password_hash = get_entity_password_hash(current_entity)
    
    # Verify current password
    if not verify_password(request.current_password, current_password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Check if new password is different from current
    if request.current_password == request.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password"
        )
    
    # Update password
    update_entity_password(db, current_entity, request.new_password)
    
    return ChangePasswordResponse(
        message="Password has been changed successfully",
        success=True
    )


@router.post("/verify-email", response_model=VerifyEmailResponse)
def verify_email(
    request: VerifyEmailRequest,
    db: Session = Depends(get_db)
) -> VerifyEmailResponse:
    """Verify email address using verification token"""
    
    # Verify token and get email
    email = verify_email_token(request.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )
    
    # Get user and update verification status
    user = user_crud.get_user_by_email(db, email=email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.email_verified:
        return VerifyEmailResponse(
            message="Email already verified",
            success=True,
            email=user.email
        )
    
    user_crud.verify_email(db, user)
    
    return VerifyEmailResponse(
        message="Email has been verified successfully",
        success=True,
        email=user.email
    )


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(
    request: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> ResendVerificationResponse:
    """Resend email verification link"""
    
    user = user_crud.get_user_by_email(db, email=request.email)
    
    # Don't reveal if user exists for security
    if not user:
        return ResendVerificationResponse(
            message="If the email exists, verification email has been sent",
            success=True,
            email=request.email
        )
    
    if user.email_verified:
        return ResendVerificationResponse(
            message="Email already verified",
            success=True,
            email=user.email
        )
    
    # Generate new verification token
    verification_token = create_verification_token(user.email)
    
    # Send verification email in background
    background_tasks.add_task(
        send_verification_email,
        user.email,
        f"{user.first_name} {user.last_name}",
        verification_token
    )
    
    return ResendVerificationResponse(
        message="Verification email has been resent",
        success=True,
        email=user.email
    )


@router.get("/me", response_model=Dict[str, Any])
def get_current_user_info(
    current_entity: Union[User, Subcontractor] = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get current authenticated user or subcontractor information"""
    
    # Check if it's a Subcontractor
    if isinstance(current_entity, Subcontractor):
        return {
            "id": current_entity.id,
            "email": current_entity.email,
            "first_name": current_entity.first_name,
            "last_name": current_entity.last_name,
            "company_name": current_entity.company_name,
            "trade_specialty": current_entity.trade_specialty,
            "phone": current_entity.phone,
            "is_active": current_entity.is_active,
            "role": "subcontractor",
            "user_type": "subcontractor"
        }
    
    # It's a User
    return {
        "id": current_entity.id,
        "email": current_entity.email,
        "first_name": current_entity.first_name,
        "last_name": current_entity.last_name,
        "phone": current_entity.phone,
        "role": current_entity.role,
        "is_active": current_entity.is_active,
        "email_verified": getattr(current_entity, 'email_verified', False),
        "user_type": "user"
    }


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    current_entity: Union[User, Subcontractor] = Depends(get_current_user)
) -> Dict[str, str]:
    """
    Logout endpoint (mainly for client-side token clearing)
    Note: JWT tokens are stateless, actual invalidation happens client-side
    """
    # You could implement token blacklisting here if needed
    # For now, just return success and let client clear tokens
    return {"message": "Successfully logged out"}