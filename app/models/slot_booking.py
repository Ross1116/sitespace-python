from sqlalchemy import Column, Index, Text, Date, Time, DateTime, Enum as SQLEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base

from app.schemas.enums import BookingStatus

class SlotBooking(Base):
    __tablename__ = "slot_bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("site_projects.id", ondelete="RESTRICT"), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    subcontractor_id = Column(UUID(as_uuid=True), ForeignKey("subcontractors.id", ondelete="RESTRICT"), nullable=True)  # Changed to nullable=True
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
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
    source = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Composite indexes for common query patterns
    __table_args__ = (
        Index("ix_slot_bookings_asset_date_status", "asset_id", "booking_date", "status"),
        Index("ix_slot_bookings_sub_date", "subcontractor_id", "booking_date"),
        Index("ix_slot_bookings_manager_date", "manager_id", "booking_date"),
    )

    # Relationships
    project = relationship("SiteProject", back_populates="slot_bookings")
    manager = relationship("User", back_populates="bookings")
    subcontractor = relationship("Subcontractor", back_populates="bookings")
    asset = relationship("Asset", back_populates="bookings")