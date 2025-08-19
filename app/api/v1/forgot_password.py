from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ...core.database import get_db
from ...crud.user import get_user_by_email, update_user
from ...schemas.user import UserUpdate
from ...schemas.forgot_password import ForgotPasswordRequest, ResetPasswordRequest, ForgotPasswordResponse
from ...utils.password import get_password_hash
import uuid
from datetime import datetime, timedelta

router = APIRouter(prefix="/forgot-password", tags=["Forgot Password"])

# In-memory storage for reset tokens (in production, use Redis or database)
reset_tokens = {}

@router.post("/request-reset", response_model=ForgotPasswordResponse)
def request_password_reset(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Request password reset for a user
    """
    try:
        # Check if user exists
        user = get_user_by_email(db, request.email)
        if not user:
            # Don't reveal if email exists or not for security
            return ForgotPasswordResponse(
                success=True,
                message="If the email exists, a reset link has been sent."
            )
        
        # Generate reset token
        reset_token = str(uuid.uuid4())
        expiry = datetime.utcnow() + timedelta(hours=1)
        
        # Store token (in production, store in database or Redis)
        reset_tokens[reset_token] = {
            "user_id": user.id,
            "email": user.email,
            "expiry": expiry
        }
        
        # In production, send email here
        # For now, just return success
        return ForgotPasswordResponse(
            success=True,
            message="Password reset link sent to your email."
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process password reset request: {str(e)}"
        )

@router.post("/reset-password", response_model=ForgotPasswordResponse)
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Reset password using token
    """
    try:
        # Check if token exists and is valid
        token_data = reset_tokens.get(request.token)
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        # Check if token is expired
        if datetime.utcnow() > token_data["expiry"]:
            # Remove expired token
            del reset_tokens[request.token]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired"
            )
        
        # Get user
        user = get_user_by_email(db, token_data["email"])
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Update password
        hashed_password = get_password_hash(request.new_password)
        user_update = UserUpdate(password=hashed_password)
        updated_user = update_user(db, user.id, user_update)
        
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password"
            )
        
        # Remove used token
        del reset_tokens[request.token]
        
        return ForgotPasswordResponse(
            success=True,
            message="Password reset successfully!"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset password: {str(e)}"
        )
