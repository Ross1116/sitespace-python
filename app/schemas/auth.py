from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, Any, Dict
from datetime import datetime
from uuid import UUID
import re

class PasswordMixin(BaseModel):
    """Mixin for password validation"""
    password: str = Field(..., min_length=8, max_length=100)
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character")
        return v

class LoginRequest(BaseModel):
    """Login request schema"""
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    """JWT token response"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int
    user_id: UUID
    role: str

class RefreshTokenRequest(BaseModel):
    """Refresh token request"""
    refresh_token: str

class ForgotPasswordRequest(BaseModel):
    """Forgot password request"""
    email: EmailStr

class ForgotPasswordResponse(BaseModel):
    """Forgot password response"""
    message: str = "Password reset instructions have been sent to your email"
    email: EmailStr
    success: bool = True
    reset_token_sent: bool = True
    expires_in_minutes: int = 30  # Token expiration time
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Password reset instructions have been sent to your email",
                "email": "user@example.com",
                "success": True,
                "reset_token_sent": True,
                "expires_in_minutes": 30
            }
        }
    }

class ResetPasswordRequest(PasswordMixin):
    """Reset password request"""
    token: str
    confirm_password: str
    
    @model_validator(mode='after')
    def passwords_match(self) -> 'ResetPasswordRequest':
        if self.password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self

class ResetPasswordResponse(BaseModel):
    """Reset password response"""
    message: str = "Password has been reset successfully"
    success: bool = True
    email: Optional[EmailStr] = None
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Password has been reset successfully",
                "success": True,
                "email": "user@example.com"
            }
        }
    }

class ChangePasswordRequest(PasswordMixin):
    """Change password request"""
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)
    confirm_password: str
    
    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character")
        return v
    
    @model_validator(mode='after')
    def passwords_match(self) -> 'ChangePasswordRequest':
        if self.new_password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self

class ChangePasswordResponse(BaseModel):
    """Change password response"""
    message: str = "Password has been changed successfully"
    success: bool = True
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Password has been changed successfully",
                "success": True
            }
        }
    }

class VerifyEmailRequest(BaseModel):
    """Email verification request"""
    token: str

class VerifyEmailResponse(BaseModel):
    """Email verification response"""
    message: str = "Email has been verified successfully"
    success: bool = True
    email: Optional[EmailStr] = None
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Email has been verified successfully",
                "success": True,
                "email": "user@example.com"
            }
        }
    }

class ResendVerificationRequest(BaseModel):
    """Resend verification email request"""
    email: EmailStr

class ResendVerificationResponse(BaseModel):
    """Resend verification response"""
    message: str = "Verification email has been resent"
    success: bool = True
    email: EmailStr
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Verification email has been resent",
                "success": True,
                "email": "user@example.com"
            }
        }
    }