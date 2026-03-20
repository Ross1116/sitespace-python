from pydantic import EmailStr, Field, field_validator, model_validator
from typing import Optional, Literal
from datetime import datetime
from uuid import UUID
import re

from .base import BaseSchema


def validate_password_strength(value: str) -> str:
    """Enforce the shared password policy across request schemas."""
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", value):
        raise ValueError("Password must contain at least one special character")
    return value


class PasswordMixin(BaseSchema):
    """Mixin for password validation"""
    password: str = Field(..., min_length=8, max_length=100)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class PasswordConfirmationMixin(PasswordMixin):
    """Shared confirm-password workflow for primary password fields."""

    confirm_password: str

    @model_validator(mode='after')
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self


class NewPasswordConfirmationMixin(BaseSchema):
    """Shared confirm-password workflow for password update requests."""

    new_password: str = Field(..., min_length=8, max_length=100)
    confirm_password: str

    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode='after')
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self

class LoginRequest(BaseSchema):
    """Login request schema"""
    email: EmailStr
    password: str

class TokenResponse(BaseSchema):
    """JWT token response"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int
    user_id: UUID
    role: str

class RefreshTokenRequest(BaseSchema):
    """Refresh token request"""
    refresh_token: str

class ForgotPasswordRequest(BaseSchema):
    """Forgot password request"""
    email: EmailStr

class ForgotPasswordResponse(BaseSchema):
    """Forgot password response"""
    message: str = "Password reset instructions have been sent to your email"
    email: EmailStr
    success: bool = True
    reset_token_sent: bool = True
    expires_in_minutes: int = 30

class ResetPasswordRequest(PasswordConfirmationMixin):
    """Reset password request"""
    token: str

class ResetPasswordResponse(BaseSchema):
    """Reset password response"""
    message: str = "Password has been reset successfully"
    success: bool = True
    email: Optional[EmailStr] = None

class ChangePasswordRequest(NewPasswordConfirmationMixin):
    """Change password request"""
    current_password: str

class ChangePasswordResponse(BaseSchema):
    """Change password response"""
    message: str = "Password has been changed successfully"
    success: bool = True

class VerifyEmailRequest(BaseSchema):
    """Email verification request"""
    token: str

class VerifyEmailResponse(BaseSchema):
    """Email verification response"""
    message: str = "Email has been verified successfully"
    success: bool = True
    email: Optional[EmailStr] = None

class ResendVerificationRequest(BaseSchema):
    """Resend verification email request"""
    email: EmailStr

class ResendVerificationResponse(BaseSchema):
    """Resend verification response"""
    message: str = "Verification email has been resent"
    success: bool = True
    email: EmailStr


class CurrentUserResponse(BaseSchema):
    """Response for /auth/me endpoint — User variant"""
    id: UUID
    email: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    role: str
    is_active: bool
    email_verified: bool = False
    user_type: Literal["user"] = "user"

class CurrentSubcontractorResponse(BaseSchema):
    """Response for /auth/me endpoint — Subcontractor variant"""
    id: UUID
    email: str
    first_name: str
    last_name: str
    company_name: Optional[str] = None
    trade_specialty: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool
    role: Literal["subcontractor"] = "subcontractor"
    user_type: Literal["subcontractor"] = "subcontractor"
