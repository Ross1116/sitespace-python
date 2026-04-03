from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class SystemHealthState(Base):
    __tablename__ = "system_health_states"

    key = Column(String(20), primary_key=True, default="primary")
    state = Column(String(20), nullable=False, default="healthy", server_default="healthy")
    reason_codes = Column(JSONB, nullable=False, default=list, server_default="[]")
    clean_upload_streak = Column(Integer, nullable=False, default=0, server_default="0")
    last_transition_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_trigger_upload_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("state IN ('healthy', 'degraded', 'recovery')", name="ck_system_health_states_state"),
        CheckConstraint("clean_upload_streak >= 0", name="ck_system_health_states_clean_upload_streak"),
    )


class ItemRequirementSet(Base):
    __tablename__ = "item_requirement_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    rules_json = Column(JSONB, nullable=False)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("item_id", "version", name="uq_item_requirement_sets_item_version"),
    )
