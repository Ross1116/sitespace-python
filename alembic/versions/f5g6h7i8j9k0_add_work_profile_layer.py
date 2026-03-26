"""add_work_profile_layer

Revision ID: f5g6h7i8j9k0
Revises: 98e517dc6261
Create Date: 2026-03-26

Stage 5 — Work profile infrastructure.

Adds:
  inference_policies         immutable versioned policy bundles
  item_context_profiles      per-(item, asset_type, duration, context) cache with Bayesian columns
  activity_work_profiles     materialised work profile per programme activity

Seeds:
  inference_policies row for version=1 (initial policy bundle)
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "f5g6h7i8j9k0"
down_revision = "98e517dc6261"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── inference_policies ───────────────────────────────────────────────────
    op.create_table(
        "inference_policies",
        sa.Column("version", sa.SmallInteger, primary_key=True),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("model_family", sa.String(50), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("validation_rules_version", sa.String(50), nullable=False),
        sa.Column("pattern_library_version", sa.String(50), nullable=False),
        sa.Column("hours_policy_version", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Seed the initial policy row.  This row is immutable — any material change
    # to prompt, model, validation rules, or hours policy must add a new row.
    op.execute(
        """
        INSERT INTO inference_policies
            (version, model_name, model_family, prompt_version,
             validation_rules_version, pattern_library_version, hours_policy_version)
        VALUES
            (1, 'claude-haiku-4-5-20251001', 'claude', 'work_profile_v1',
             'rules_v1', 'patterns_v1', 'hours_v1')
        """
    )

    # ── item_context_profiles ────────────────────────────────────────────────
    op.create_table(
        "item_context_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("duration_days", sa.SmallInteger, nullable=False),
        sa.Column("context_version", sa.SmallInteger, nullable=False),
        sa.Column(
            "inference_version",
            sa.SmallInteger,
            sa.ForeignKey("inference_policies.version", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("context_hash", sa.String(64), nullable=False),
        # Profile values
        sa.Column("total_hours", sa.Numeric(8, 2), nullable=False),
        sa.Column("distribution_json", JSONB, nullable=False),
        sa.Column("normalized_distribution_json", JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "low_confidence_flag",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        # Evidence accumulation
        sa.Column(
            "observation_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "evidence_weight",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="0",
        ),
        # Bayesian posterior (Normal-Normal conjugate)
        sa.Column("posterior_mean", sa.Numeric(10, 4), nullable=True),
        sa.Column("posterior_precision", sa.Numeric(20, 8), nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "correction_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "actuals_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("actuals_median", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.UniqueConstraint(
            "item_id",
            "asset_type",
            "duration_days",
            "context_version",
            "inference_version",
            "context_hash",
            name="uq_item_context_profiles_key",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'learned', 'ai', 'default')",
            name="ck_item_context_profiles_source",
        ),
        sa.CheckConstraint(
            "duration_days > 0",
            name="ck_item_context_profiles_duration_days",
        ),
        sa.CheckConstraint(
            "total_hours >= 0",
            name="ck_item_context_profiles_total_hours",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_item_context_profiles_confidence",
        ),
    )
    op.create_index(
        "ix_item_context_profiles_item_id",
        "item_context_profiles",
        ["item_id"],
    )
    op.create_index(
        "ix_item_context_profiles_lookup",
        "item_context_profiles",
        ["item_id", "asset_type", "duration_days", "context_version", "inference_version"],
    )

    # ── activity_work_profiles ───────────────────────────────────────────────
    op.create_table(
        "activity_work_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "activity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("programme_activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("duration_days", sa.SmallInteger, nullable=False),
        sa.Column("context_version", sa.SmallInteger, nullable=False),
        sa.Column(
            "inference_version",
            sa.SmallInteger,
            sa.ForeignKey("inference_policies.version", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("total_hours", sa.Numeric(8, 2), nullable=False),
        sa.Column("distribution_json", JSONB, nullable=False),
        sa.Column("normalized_distribution_json", JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column(
            "low_confidence_flag",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column(
            "context_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("item_context_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.CheckConstraint(
            "source IN ('ai', 'cache', 'manual', 'default')",
            name="ck_activity_work_profiles_source",
        ),
        sa.CheckConstraint(
            "duration_days > 0",
            name="ck_activity_work_profiles_duration_days",
        ),
        sa.CheckConstraint(
            "total_hours >= 0",
            name="ck_activity_work_profiles_total_hours",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_activity_work_profiles_confidence",
        ),
    )
    op.create_index(
        "ix_activity_work_profiles_activity_id",
        "activity_work_profiles",
        ["activity_id"],
    )
    op.create_index(
        "ix_activity_work_profiles_item_id",
        "activity_work_profiles",
        ["item_id"],
    )
    op.create_index(
        "ix_activity_work_profiles_context_profile_id",
        "activity_work_profiles",
        ["context_profile_id"],
    )


def downgrade() -> None:
    op.drop_table("activity_work_profiles")
    op.drop_table("item_context_profiles")
    op.drop_table("inference_policies")
