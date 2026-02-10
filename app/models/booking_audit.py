import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, DateTime, Enum as SQLEnum, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.schemas.enums import BookingStatus, UserRole, BookingAuditAction


class BookingAuditLog(Base):
    __tablename__ = "booking_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("slot_bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Actor snapshot (no FK on purpose)
    actor_id = Column(UUID(as_uuid=True), nullable=False)
    actor_role = Column(SQLEnum(UserRole), nullable=False)
    actor_name = Column(Text, nullable=False)

    # Action + state
    action = Column(SQLEnum(BookingAuditAction), nullable=False)
    from_status = Column(SQLEnum(BookingStatus), nullable=True)
    to_status = Column(SQLEnum(BookingStatus), nullable=True)

    # Details
    changes = Column(JSON, nullable=True)
    comment = Column(Text, nullable=True)

    # Immutable timestamp — timezone-aware
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        Index("ix_audit_actor_created", "actor_id", "created_at"),
    )

    booking = relationship("SlotBooking", backref="audit_logs")