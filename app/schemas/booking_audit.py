from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import UUID

from pydantic import Field

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
    from_status: Optional[BookingStatus]
    to_status: Optional[BookingStatus]

    changes: Optional[Dict[str, Any]]
    comment: Optional[str]

    created_at: datetime


class BookingAuditTrailResponse(BaseSchema):
    booking_id: UUID
    history: List[BookingAuditResponse]