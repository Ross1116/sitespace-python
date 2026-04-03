"""close remaining non-notification gaps a

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "q7r8s9t0u1v2"
down_revision: Union[str, None] = "p6q7r8s9t0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("planning_attributes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "asset_types",
        sa.Column("planning_attributes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column(
        "item_context_profiles",
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "item_context_profiles",
        sa.Column("invalidation_reason", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "item_context_profiles",
        sa.Column("superseded_by_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_item_context_profiles_invalidated_at",
        "item_context_profiles",
        ["invalidated_at"],
        unique=False,
    )
    op.create_index(
        "ix_item_context_profiles_superseded_by_profile_id",
        "item_context_profiles",
        ["superseded_by_profile_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_item_context_profiles_superseded_by_profile_id",
        "item_context_profiles",
        "item_context_profiles",
        ["superseded_by_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "system_health_states",
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="healthy"),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("clean_upload_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_trigger_upload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('healthy', 'degraded', 'recovery')", name="ck_system_health_states_state"),
        sa.CheckConstraint("clean_upload_streak >= 0", name="ck_system_health_states_clean_upload_streak"),
        sa.ForeignKeyConstraint(["last_trigger_upload_id"], ["programme_uploads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "item_requirement_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("rules_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "version", name="uq_item_requirement_sets_item_version"),
    )
    op.create_index("ix_item_requirement_sets_item_id", "item_requirement_sets", ["item_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_item_requirement_sets_item_id", table_name="item_requirement_sets")
    op.drop_table("item_requirement_sets")
    op.drop_table("system_health_states")
    op.drop_constraint(
        "fk_item_context_profiles_superseded_by_profile_id",
        "item_context_profiles",
        type_="foreignkey",
    )
    op.drop_index("ix_item_context_profiles_superseded_by_profile_id", table_name="item_context_profiles")
    op.drop_index("ix_item_context_profiles_invalidated_at", table_name="item_context_profiles")
    op.drop_column("item_context_profiles", "superseded_by_profile_id")
    op.drop_column("item_context_profiles", "invalidation_reason")
    op.drop_column("item_context_profiles", "invalidated_at")
    op.drop_column("asset_types", "planning_attributes_json")
    op.drop_column("assets", "planning_attributes_json")
