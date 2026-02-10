from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from uuid import UUID

from pydantic import Field, field_serializer, field_validator

from .base import BaseSchema
from .enums import BookingStatus, UserRole, BookingAuditAction


class BookingAuditBase(BaseSchema):
    action: BookingAuditAction
    comment: Optional[str] = Field(None, max_length=1000)


class BookingAuditCreate(BookingAuditBase):
    booking_id: UUID
    actor_id: UUID
    actor_role: UserRole
    actor_name: str

    from_status: Optional[BookingStatus] = None
    to_status: Optional[BookingStatus] = None
    changes: Optional[Dict[str, Any]] = None


class BookingAuditResponse(BaseSchema):
    id: UUID
    booking_id: UUID

    actor_id: UUID
    actor_role: UserRole
    actor_name: str

    action: BookingAuditAction
    from_status: Optional[BookingStatus] = None
    to_status: Optional[BookingStatus] = None

    changes: Optional[Dict[str, Any]] = None
    comment: Optional[str] = None

    created_at: Optional[datetime] = None

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v):
        return v if v is not None else datetime.now(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: Optional[datetime], _info) -> Optional[str]:
        """
        Guarantee the ISO string always includes timezone info.
        Naive datetimes (from old rows) are assumed UTC.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class BookingAuditTrailResponse(BaseSchema):
    booking_id: UUID
    history: List[BookingAuditResponse]