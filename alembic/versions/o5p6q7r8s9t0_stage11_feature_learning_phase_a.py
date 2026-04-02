"""stage11 feature learning phase a

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- item_context_profiles: actuals_shape_json ---
    op.add_column(
        "item_context_profiles",
        sa.Column("actuals_shape_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # --- item_knowledge_base: actuals shape columns ---
    op.add_column(
        "item_knowledge_base",
        sa.Column("actuals_shape_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "item_knowledge_base",
        sa.Column(
            "actuals_shape_weight",
            sa.Numeric(10, 4),
            nullable=True,
            server_default="0",
        ),
    )

    # --- context_feature_observations ---
    op.create_table(
        "context_feature_observations",
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
        sa.Column("ctx_phase", sa.String(30), nullable=False),
        sa.Column("ctx_spatial_type", sa.String(30), nullable=False),
        sa.Column("ctx_area_type", sa.String(30), nullable=False),
        sa.Column("ctx_work_type", sa.String(30), nullable=False),
        sa.Column("predicted_hours", sa.Numeric(10, 4), nullable=False),
        sa.Column("actual_hours", sa.Numeric(10, 4), nullable=False),
        sa.Column("residual", sa.Numeric(10, 4), nullable=False),
        sa.Column("relative_error", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "context_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("item_context_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "activity_work_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("activity_work_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("context_version", sa.SmallInteger(), nullable=False),
        sa.Column("inference_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_bucket > 0",
            name="ck_ctx_feature_obs_duration_bucket",
        ),
        sa.CheckConstraint(
            "predicted_hours >= 0",
            name="ck_ctx_feature_obs_predicted_hours",
        ),
        sa.CheckConstraint(
            "actual_hours >= 0",
            name="ck_ctx_feature_obs_actual_hours",
        ),
    )
    op.create_index(
        "ix_ctx_feature_obs_item_asset_bucket",
        "context_feature_observations",
        ["item_id", "asset_type", "duration_bucket"],
    )
    op.create_index(
        "ix_ctx_feature_obs_context_fields",
        "context_feature_observations",
        ["ctx_phase", "ctx_spatial_type", "ctx_area_type", "ctx_work_type"],
    )
    op.create_index(
        "ix_ctx_feature_obs_project_id",
        "context_feature_observations",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ctx_feature_obs_project_id", table_name="context_feature_observations")
    op.drop_index("ix_ctx_feature_obs_context_fields", table_name="context_feature_observations")
    op.drop_index("ix_ctx_feature_obs_item_asset_bucket", table_name="context_feature_observations")
    op.drop_table("context_feature_observations")

    op.drop_column("item_knowledge_base", "actuals_shape_weight")
    op.drop_column("item_knowledge_base", "actuals_shape_json")

    op.drop_column("item_context_profiles", "actuals_shape_json")
