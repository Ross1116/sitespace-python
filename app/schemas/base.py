from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, timezone
from uuid import UUID
from typing import Any, Dict, Optional

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

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v):
        return v if v is not None else datetime.now(timezone.utc)

    @field_validator("updated_at", mode="before")
    @classmethod
    def default_updated_at(cls, v):
        return v if v is not None else datetime.now(timezone.utc)

class PaginationParams(BaseSchema):
    """Pagination parameters"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)

class PaginatedResponse(BaseSchema):
    """Generic paginated response"""
    total: int
    skip: int
    limit: int
    has_more: bool

class MessageResponse(BaseSchema):
    """Simple message response"""
    message: str
    success: bool = True
    data: Optional[Dict[str, Any]] = None

class ErrorResponse(BaseSchema):
    """Error response schema"""
    error: str
    detail: Optional[str] = None
    status_code: int