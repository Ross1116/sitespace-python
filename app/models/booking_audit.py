import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, Text, JSON
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

    # Immutable timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    booking = relationship("SlotBooking", backref="audit_logs")