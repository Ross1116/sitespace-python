from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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


class InferencePolicy(Base):
    """
    Immutable versioned bundle describing the inference policy used to generate
    a cached work profile.  Rows are never mutated after creation.

    Any material change to prompt structure, model, validation rules, pattern
    library, or hours-bounds policy requires a new version row and a bump of
    the active INFERENCE_VERSION constant in work_profile_service.
    """

    __tablename__ = "inference_policies"

    version = Column(SmallInteger, primary_key=True)
    model_name = Column(String(100), nullable=False)
    model_family = Column(String(50), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    validation_rules_version = Column(String(50), nullable=False)
    pattern_library_version = Column(String(50), nullable=False)
    hours_policy_version = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<InferencePolicy(version={self.version}, model='{self.model_name}')>"


class ItemContextProfile(Base):
    """
    Cached work profile for a unique (item, asset_type, duration, context) combination.

    One row per deterministic context key.  Updated in place on each encounter:
    - observation_count / evidence_weight track reuse frequency
    - posterior_mean / posterior_precision encode the Bayesian estimate of total_hours
    - sample_count / correction_count / actuals_count drive maturity tier evaluation

    Source priority: manual > learned > ai > default
    """

    __tablename__ = "item_context_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type = Column(
        String(50),
        ForeignKey("asset_types.code", ondelete="RESTRICT"),
        nullable=False,
    )
    duration_days = Column(SmallInteger, nullable=False)
    context_version = Column(SmallInteger, nullable=False)
    inference_version = Column(
        SmallInteger,
        ForeignKey("inference_policies.version", ondelete="RESTRICT"),
        nullable=False,
    )
    context_hash = Column(String(64), nullable=False)

    # Stored profile values
    total_hours = Column(Numeric(8, 2), nullable=False)
    distribution_json = Column(JSONB, nullable=False)
    normalized_distribution_json = Column(JSONB, nullable=False)
    confidence = Column(Numeric(4, 3), nullable=False)
    source = Column(String(20), nullable=False)   # 'manual' | 'learned' | 'ai' | 'default'
    low_confidence_flag = Column(Boolean, nullable=False, default=False, server_default="false")

    # Evidence accumulation
    observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    evidence_weight = Column(Numeric(10, 4), nullable=False, default=0, server_default="0")

    # Bayesian posterior (Normal-Normal conjugate)
    posterior_mean = Column(Numeric(10, 4), nullable=True)
    posterior_precision = Column(Numeric(20, 8), nullable=True)
    sample_count = Column(Integer, nullable=False, default=0, server_default="0")
    correction_count = Column(Integer, nullable=False, default=0, server_default="0")
    actuals_count = Column(Integer, nullable=False, default=0, server_default="0")
    actuals_median = Column(Numeric(10, 4), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "asset_type",
            "duration_days",
            "context_version",
            "inference_version",
            "context_hash",
            name="uq_item_context_profiles_key",
        ),
        CheckConstraint(
            "source IN ('manual', 'learned', 'ai', 'default')",
            name="ck_item_context_profiles_source",
        ),
        CheckConstraint(
            "duration_days > 0",
            name="ck_item_context_profiles_duration_days",
        ),
        CheckConstraint(
            "total_hours >= 0",
            name="ck_item_context_profiles_total_hours",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_item_context_profiles_confidence",
        ),
    )

    item = relationship("Item", foreign_keys=[item_id])
    inference_policy = relationship("InferencePolicy", foreign_keys=[inference_version])

    def __repr__(self) -> str:
        return (
            f"<ItemContextProfile(item={self.item_id}, asset='{self.asset_type}', "
            f"dur={self.duration_days}, source='{self.source}')>"
        )


class ActivityWorkProfile(Base):
    """
    Materialised work profile for one programme activity.

    Written once per activity per upload.  References the item_context_profiles
    entry that was used or created to produce it.
    """

    __tablename__ = "activity_work_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type = Column(
        String(50),
        ForeignKey("asset_types.code", ondelete="RESTRICT"),
        nullable=False,
    )
    duration_days = Column(SmallInteger, nullable=False)
    context_version = Column(SmallInteger, nullable=False)
    inference_version = Column(
        SmallInteger,
        ForeignKey("inference_policies.version", ondelete="RESTRICT"),
        nullable=False,
    )
    total_hours = Column(Numeric(8, 2), nullable=False)
    distribution_json = Column(JSONB, nullable=False)
    normalized_distribution_json = Column(JSONB, nullable=False)
    confidence = Column(Numeric(4, 3), nullable=False)
    low_confidence_flag = Column(Boolean, nullable=False, default=False, server_default="false")
    source = Column(String(20), nullable=False)   # 'ai' | 'cache' | 'manual' | 'default'
    context_hash = Column(String(64), nullable=False)
    context_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("item_context_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "source IN ('ai', 'cache', 'manual', 'default')",
            name="ck_activity_work_profiles_source",
        ),
        CheckConstraint(
            "duration_days > 0",
            name="ck_activity_work_profiles_duration_days",
        ),
        CheckConstraint(
            "total_hours >= 0",
            name="ck_activity_work_profiles_total_hours",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_activity_work_profiles_confidence",
        ),
    )

    activity = relationship("ProgrammeActivity", foreign_keys=[activity_id])
    item = relationship("Item", foreign_keys=[item_id])
    inference_policy = relationship("InferencePolicy", foreign_keys=[inference_version])
    context_profile = relationship("ItemContextProfile", foreign_keys=[context_profile_id])

    def __repr__(self) -> str:
        return (
            f"<ActivityWorkProfile(activity={self.activity_id}, asset='{self.asset_type}', "
            f"hours={self.total_hours}, source='{self.source}')>"
        )
