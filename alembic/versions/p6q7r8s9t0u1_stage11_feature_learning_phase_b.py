"""stage11 feature learning phase b

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "p6q7r8s9t0u1"
down_revision: Union[str, None] = "o5p6q7r8s9t0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- context_feature_effects ---
    op.create_table(
        "context_feature_effects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("duration_bucket", sa.SmallInteger(), nullable=False),
        sa.Column("feature_name", sa.String(30), nullable=False),
        sa.Column("feature_value", sa.String(30), nullable=False),
        sa.Column("context_version", sa.SmallInteger(), nullable=False),
        sa.Column("inference_version", sa.SmallInteger(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("mean_residual", sa.Numeric(10, 4), nullable=False),
        sa.Column("variance_of_residual", sa.Numeric(14, 6), nullable=False),
        sa.Column("effect_magnitude", sa.Numeric(8, 6), nullable=False),
        sa.Column("learned_weight", sa.Numeric(8, 6), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False),
        sa.Column("effective_weight", sa.Numeric(8, 6), nullable=False),
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
        sa.UniqueConstraint(
            "item_id", "asset_type", "duration_bucket",
            "feature_name", "feature_value",
            "context_version", "inference_version",
            name="uq_context_feature_effects_key",
        ),
        sa.CheckConstraint("duration_bucket > 0", name="ck_ctx_feature_effects_duration_bucket"),
        sa.CheckConstraint("observation_count > 0", name="ck_ctx_feature_effects_obs_count"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ctx_feature_effects_confidence",
        ),
        sa.CheckConstraint(
            "feature_name IN ('phase', 'spatial_type', 'area_type', 'work_type')",
            name="ck_ctx_feature_effects_feature_name",
        ),
    )
    op.create_index(
        "ix_ctx_feature_effects_item_asset_bucket",
        "context_feature_effects",
        ["item_id", "asset_type", "duration_bucket"],
    )

    # --- context_expansion_signals ---
    op.create_table(
        "context_expansion_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("context_signature", sa.String(120), nullable=False),
        sa.Column("context_version", sa.SmallInteger(), nullable=False),
        sa.Column("inference_version", sa.SmallInteger(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("mean_cv", sa.Numeric(8, 6), nullable=False),
        sa.Column("expansion_candidate_field", sa.String(30), nullable=False),
        sa.Column("expansion_score", sa.Numeric(8, 6), nullable=False),
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "item_id", "asset_type", "context_signature",
            "context_version", "inference_version",
            name="uq_context_expansion_signals_key",
        ),
        sa.CheckConstraint("observation_count > 0", name="ck_ctx_expansion_signals_obs_count"),
        sa.CheckConstraint(
            "expansion_score >= 0 AND expansion_score <= 1",
            name="ck_ctx_expansion_signals_score",
        ),
    )
    op.create_index(
        "ix_ctx_expansion_signals_item_asset",
        "context_expansion_signals",
        ["item_id", "asset_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_ctx_expansion_signals_item_asset", table_name="context_expansion_signals")
    op.drop_table("context_expansion_signals")
    op.drop_index("ix_ctx_feature_effects_item_asset_bucket", table_name="context_feature_effects")
    op.drop_table("context_feature_effects")
