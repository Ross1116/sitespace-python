from sqlalchemy import Column, Text, Date, Time, DateTime, Enum as SQLEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from app.core.database import Base

class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DENIED = "DENIED"

class SlotBooking(Base):
    __tablename__ = "slot_bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("site_projects.id", ondelete="CASCADE"), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subcontractor_id = Column(UUID(as_uuid=True), ForeignKey("subcontractors.id", ondelete="CASCADE"), nullable=True)  # Changed to nullable=True
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    booking_date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(
        SQLEnum(
            BookingStatus,
            name="bookingstatus",
            native_enum=True,
            validate_strings=True
        ),
        nullable=False,
        default=BookingStatus.PENDING
    )
    purpose = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("SiteProject", back_populates="slot_bookings")
    manager = relationship("User", back_populates="bookings")
    subcontractor = relationship("Subcontractor", back_populates="bookings")
    asset = relationship("Asset", back_populates="bookings")