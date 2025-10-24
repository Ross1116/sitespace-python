from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import datetime
from uuid import UUID
import re

from .base import BaseSchema, TimestampSchema
from .enums import UserRole
from .auth import PasswordMixin

class UserBase(BaseSchema):
    """Base user schema"""
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    
    @field_validator('phone')
    def validate_phone(cls, v):
        if v and not re.match(r'^\+?1?\d{9,15}$', v):
            raise ValueError('Invalid phone number format')
        return v

class UserCreate(UserBase, PasswordMixin):
    """User creation schema"""
    role: UserRole = UserRole.MANAGER
    confirm_password: str
    
    @field_validator('confirm_password')
    def passwords_match(cls, v, values):
        if 'password' in values.data and v != values.data['password']:
            raise ValueError('Passwords do not match')
        return v

class UserUpdate(BaseSchema):
    """User update schema"""
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None

class UserResponse(UserBase, TimestampSchema):
    """User response schema"""
    id: UUID
    role: UserRole
    is_active: bool

class UserDetailResponse(UserResponse):
    """Detailed user response with relationships"""
    managed_projects_count: int = 0
    active_projects: List['ProjectBriefResponse'] = []
    subcontractors_count: int = 0

class UserListResponse(BaseSchema):
    """User list response with pagination"""
    users: List[UserResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

class UserBriefResponse(BaseSchema):
    """Brief user info for nested responses"""
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    role: UserRole

# Avoid circular imports
from .site_project import SiteProjectBriefResponse as ProjectBriefResponse
UserDetailResponse.model_rebuild()