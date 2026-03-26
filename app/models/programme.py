from sqlalchemy import Column, String, Integer, SmallInteger, Float, Date, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class ProgrammeUpload(Base):
    __tablename__ = "programme_uploads"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_programme_upload_project_version"),
        CheckConstraint("work_days_per_week BETWEEN 1 AND 7", name="ck_programme_uploads_work_days"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    uploaded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stored_files.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name = Column(String(255), nullable=False)
    column_mapping = Column(JSONB, nullable=True)
    version_number = Column(Integer, nullable=False, default=1)
    completeness_score = Column(Float, nullable=True)       # 0.0-1.0
    completeness_notes = Column(JSONB, nullable=True)       # list of degradation reason strings
    status = Column(String(20), nullable=False, default="processing")  # processing | committed
    processing_outcome = Column(String(30), nullable=True)
    ai_tokens_used = Column(Integer, nullable=True)
    work_days_per_week = Column(SmallInteger, nullable=False, default=5)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project = relationship("SiteProject", foreign_keys=[project_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
    file = relationship("StoredFile", foreign_keys=[file_id])
    activities = relationship(
        "ProgrammeActivity",
        back_populates="upload",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="ProgrammeActivity.programme_upload_id",
    )

    def __repr__(self) -> str:
        return f"<ProgrammeUpload(id={self.id}, file='{self.file_name}', status='{self.status}')>"


class ProgrammeActivity(Base):
    __tablename__ = "programme_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_upload_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_uploads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Self-referential FK — declared via __table_args__ with DEFERRABLE so bulk
    # inserts don't fail when a child row is committed before its parent.
    parent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    name = Column(String(512), nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    duration_days = Column(Integer, nullable=True)
    level_name = Column(String(100), nullable=True)
    zone_name = Column(String(100), nullable=True)
    is_summary = Column(Boolean, nullable=False, default=False)
    wbs_code = Column(String(100), nullable=True)
    sort_order = Column(Integer, nullable=True)
    # import_flags: e.g. ["dates_missing", "unstructured", "date_parse_failed"]
    import_flags = Column(JSONB, nullable=True)
    # Stage 1 correctness columns
    pct_complete = Column(SmallInteger, nullable=True)          # 0–100 extracted from file
    activity_kind = Column(String(20), nullable=True)           # 'summary' | 'task' | 'milestone'
    row_confidence = Column(String(10), nullable=True)          # 'high' | 'medium' | 'low'
    # Stage 2 identity columns
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_id"],
            ["programme_activities.id"],
            name="fk_programme_activities_parent_id",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("pct_complete BETWEEN 0 AND 100", name="ck_programme_activities_pct_complete"),
        CheckConstraint("row_confidence IN ('high', 'medium', 'low')", name="ck_programme_activities_row_confidence"),
        CheckConstraint("activity_kind IN ('summary', 'task', 'milestone')", name="ck_programme_activities_activity_kind"),
    )

    # Relationships
    upload = relationship(
        "ProgrammeUpload",
        back_populates="activities",
        foreign_keys=[programme_upload_id],
    )
    parent = relationship(
        "ProgrammeActivity",
        remote_side="ProgrammeActivity.id",
        foreign_keys=[parent_id],
        backref=backref("children", passive_deletes=True),
    )

    def __repr__(self) -> str:
        return f"<ProgrammeActivity(id={self.id}, name='{self.name}')>"


class ActivityAssetMapping(Base):
    __tablename__ = "activity_asset_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable for low-confidence rows that haven't been classified yet.
    # Application layer validates against ALLOWED_ASSET_TYPES when non-null.
    asset_type = Column(String(50), nullable=True)
    confidence = Column(String(10), nullable=False)   # high | medium | low
    source = Column(String(20), nullable=False)        # ai | keyword | manual
    auto_committed = Column(Boolean, nullable=False, default=False)
    manually_corrected = Column(Boolean, nullable=False, default=False)
    corrected_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    subcontractor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subcontractors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    activity = relationship("ProgrammeActivity", foreign_keys=[programme_activity_id])
    corrector = relationship("User", foreign_keys=[corrected_by])
    subcontractor = relationship("Subcontractor", foreign_keys=[subcontractor_id])

    def __repr__(self) -> str:
        return f"<ActivityAssetMapping(id={self.id}, asset='{self.asset_type}', confidence='{self.confidence}')>"


class AISuggestionLog(Base):
    __tablename__ = "ai_suggestion_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Upload that triggered this classification run (nullable for legacy rows).
    upload_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_uploads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    suggested_asset_type = Column(String(50), nullable=True)
    confidence = Column(String(10), nullable=True)
    # accepted=True: PM left it; False: PM corrected it
    accepted = Column(Boolean, nullable=False, default=True)
    # correction populated when accepted=False
    correction = Column(String(50), nullable=True)
    # Observability fields — populated by process_programme._write_classifications()
    source = Column(String(20), nullable=True)          # "ai" | "keyword_boost"
    pipeline_stage = Column(String(30), nullable=True)  # "classify_assets"
    model_name = Column(String(100), nullable=True)     # e.g. "claude-haiku-4-5-20251001"
    fallback_used = Column(Boolean, nullable=True)      # True when AI unavailable
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    activity = relationship("ProgrammeActivity", foreign_keys=[activity_id])
    upload = relationship("ProgrammeUpload", foreign_keys=[upload_id])

    def __repr__(self) -> str:
        return f"<AISuggestionLog(id={self.id}, accepted={self.accepted})>"
