"""Stage 10 learning local/global boundary A

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-03-31 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_context_profiles",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_item_context_profiles_project_id",
        "item_context_profiles",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_item_context_profiles_project_id",
        "item_context_profiles",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ux_item_context_profiles_project_local_key_tmp",
        "item_context_profiles",
        [
            "project_id",
            "item_id",
            "asset_type",
            "duration_days",
            "context_version",
            "inference_version",
            "context_hash",
        ],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )

    op.create_table(
        "item_knowledge_base",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=False),
        sa.Column("duration_bucket", sa.SmallInteger(), nullable=False),
        sa.Column("context_version", sa.SmallInteger(), nullable=False),
        sa.Column("inference_version", sa.SmallInteger(), nullable=False),
        sa.Column("posterior_mean", sa.Numeric(10, 4), nullable=False),
        sa.Column("posterior_precision", sa.Numeric(20, 8), nullable=False),
        sa.Column("source_project_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("normalized_shape_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence_tier", sa.String(length=10), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("duration_bucket > 0", name="ck_item_knowledge_base_duration_bucket"),
        sa.CheckConstraint("posterior_mean >= 0", name="ck_item_knowledge_base_posterior_mean"),
        sa.CheckConstraint("posterior_precision > 0", name="ck_item_knowledge_base_posterior_precision"),
        sa.CheckConstraint("confidence_tier IN ('medium', 'high')", name="ck_item_knowledge_base_confidence_tier"),
        sa.ForeignKeyConstraint(["asset_type"], ["asset_types.code"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["inference_version"], ["inference_policies.version"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "asset_type",
            "duration_bucket",
            "context_version",
            "inference_version",
            name="uq_item_knowledge_base_key",
        ),
    )
    op.create_index("ix_item_knowledge_base_item_id", "item_knowledge_base", ["item_id"], unique=False)
    op.create_index("ix_item_knowledge_base_asset_type", "item_knowledge_base", ["asset_type"], unique=False)

    op.create_table(
        "asset_usage_actuals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_work_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actual_hours_used", sa.Numeric(10, 4), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="system"),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("actual_hours_used >= 0", name="ck_asset_usage_actuals_actual_hours_used"),
        sa.CheckConstraint("source IN ('system', 'manual', 'import')", name="ck_asset_usage_actuals_source"),
        sa.ForeignKeyConstraint(["activity_work_profile_id"], ["activity_work_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["booking_group_id"], ["activity_booking_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["booking_id"], ["slot_bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("activity_work_profile_id", name="uq_asset_usage_actuals_activity_work_profile_id"),
    )
    op.create_index(
        "ix_asset_usage_actuals_activity_work_profile_id",
        "asset_usage_actuals",
        ["activity_work_profile_id"],
        unique=False,
    )
    op.create_index("ix_asset_usage_actuals_booking_group_id", "asset_usage_actuals", ["booking_group_id"], unique=False)
    op.create_index("ix_asset_usage_actuals_booking_id", "asset_usage_actuals", ["booking_id"], unique=False)
    op.create_index(
        "ix_asset_usage_actuals_recorded_by_user_id",
        "asset_usage_actuals",
        ["recorded_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_asset_usage_actuals_recorded_by_user_id", table_name="asset_usage_actuals")
    op.drop_index("ix_asset_usage_actuals_booking_id", table_name="asset_usage_actuals")
    op.drop_index("ix_asset_usage_actuals_booking_group_id", table_name="asset_usage_actuals")
    op.drop_index("ix_asset_usage_actuals_activity_work_profile_id", table_name="asset_usage_actuals")
    op.drop_table("asset_usage_actuals")

    op.drop_index("ix_item_knowledge_base_asset_type", table_name="item_knowledge_base")
    op.drop_index("ix_item_knowledge_base_item_id", table_name="item_knowledge_base")
    op.drop_table("item_knowledge_base")

    op.drop_index("ux_item_context_profiles_project_local_key_tmp", table_name="item_context_profiles")
    op.drop_index("ix_item_context_profiles_project_id", table_name="item_context_profiles")
    op.drop_constraint("fk_item_context_profiles_project_id", "item_context_profiles", type_="foreignkey")
    op.drop_column("item_context_profiles", "project_id")
