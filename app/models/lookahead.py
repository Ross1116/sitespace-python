from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
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
    rows = relationship(
        "LookaheadRow",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LookaheadRow(Base):
    __tablename__ = "lookahead_rows"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "week_start",
            "asset_type",
            name="uq_lookahead_rows_snapshot_week_asset",
        ),
        CheckConstraint("demand_hours >= 0", name="ck_lookahead_rows_demand_hours"),
        CheckConstraint("booked_hours >= 0", name="ck_lookahead_rows_booked_hours"),
        CheckConstraint("gap_hours >= 0", name="ck_lookahead_rows_gap_hours"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lookahead_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    week_start = Column(Date, nullable=False, index=True)
    asset_type = Column(String(50), nullable=False)
    demand_hours = Column(Numeric(10, 2), nullable=False)
    booked_hours = Column(Numeric(10, 2), nullable=False)
    gap_hours = Column(Numeric(10, 2), nullable=False)
    is_anomalous = Column(Boolean, nullable=False, default=False, server_default="false")
    anomaly_flags_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    snapshot = relationship("LookaheadSnapshot", back_populates="rows", foreign_keys=[snapshot_id])
    project = relationship("SiteProject", foreign_keys=[project_id])


class ProjectAlertPolicy(Base):
    __tablename__ = "project_alert_policies"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('observe_only', 'thresholded', 'active')",
            name="ck_project_alert_policies_mode",
        ),
    )

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode = Column(String(20), nullable=False, default="observe_only", server_default="observe_only")
    external_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    min_demand_hours = Column(Numeric(10, 2), nullable=False, default=8, server_default="8")
    min_gap_hours = Column(Numeric(10, 2), nullable=False, default=8, server_default="8")
    min_gap_ratio = Column(Numeric(6, 4), nullable=False, default=0.25, server_default="0.25")
    min_lead_weeks = Column(SmallInteger, nullable=False, default=1, server_default="1")
    max_alerts_per_subcontractor_per_week = Column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    max_alerts_per_project_per_week = Column(
        SmallInteger, nullable=False, default=20, server_default="20"
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("SiteProject", foreign_keys=[project_id])


class SubcontractorAssetTypeAssignment(Base):
    __tablename__ = "subcontractor_asset_type_assignments"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "subcontractor_id",
            "asset_type",
            name="uq_subcontractor_asset_type_assignments",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subcontractor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subcontractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type = Column(String(50), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("SiteProject", foreign_keys=[project_id])
    subcontractor = relationship("Subcontractor", foreign_keys=[subcontractor_id])


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'acted', 'cancelled', 'failed')",
            name="ck_notifications_status_stage5",
        ),
        Index(
            "ix_notifications_project_week_asset",
            "project_id",
            "week_start",
            "asset_type",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sub_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subcontractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_activities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    asset_type = Column(String(50), nullable=False)
    week_start = Column(Date, nullable=True, index=True)
    trigger_type = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    severity_score = Column(Numeric(10, 4), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=True)
    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("slot_bookings.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    subcontractor = relationship("Subcontractor", foreign_keys=[sub_id])
    project = relationship("SiteProject", foreign_keys=[project_id])
    activity = relationship("ProgrammeActivity", foreign_keys=[activity_id])
    booking = relationship("SlotBooking", foreign_keys=[booking_id])
