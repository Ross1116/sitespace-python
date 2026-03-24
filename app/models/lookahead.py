from sqlalchemy import Column, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class LookaheadSnapshot(Base):
    __tablename__ = "lookahead_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", "snapshot_date", name="uq_lookahead_snapshots_project_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    programme_upload_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_date = Column(Date, nullable=False, index=True)
    data = Column(JSONB, nullable=False)
    anomaly_flags = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("SiteProject", foreign_keys=[project_id])
    programme_upload = relationship("ProgrammeUpload", foreign_keys=[programme_upload_id])


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sub_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subcontractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_activities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    asset_type = Column(String(50), nullable=False)
    trigger_type = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=True)
    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("slot_bookings.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    subcontractor = relationship("Subcontractor", foreign_keys=[sub_id])
    activity = relationship("ProgrammeActivity", foreign_keys=[activity_id])
    booking = relationship("SlotBooking", foreign_keys=[booking_id])
