import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, DateTime, String, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class BookingAuditLog(Base):
    __tablename__ = "booking_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("slot_bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )

    # Actor snapshot (no FK on purpose)
    actor_id = Column(UUID(as_uuid=True), nullable=False)
    actor_role = Column(String, nullable=False)
    actor_name = Column(Text, nullable=False)

    # Action + state
    action = Column(String, nullable=False)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)

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