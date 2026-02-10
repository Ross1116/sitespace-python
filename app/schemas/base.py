from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from uuid import UUID
from typing import Optional

class BaseSchema(BaseModel):
    """Base schema with common configuration"""
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=True
    )

class TimestampSchema(BaseSchema):
    """Schema with timestamp fields"""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class PaginationParams(BaseModel):
    """Pagination parameters"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)

class PaginatedResponse(BaseModel):
    """Generic paginated response"""
    total: int
    skip: int
    limit: int
    has_more: bool

class MessageResponse(BaseSchema):
    """Simple message response"""
    message: str
    success: bool = True

class ErrorResponse(BaseSchema):
    """Error response schema"""
    error: str
    detail: Optional[str] = None
    status_code: int