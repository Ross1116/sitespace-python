"""project_scoped_asset_types

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset_types", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "asset_types",
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="global"),
    )
    op.add_column(
        "asset_types",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("asset_types", sa.Column("local_slug", sa.String(length=50), nullable=True))
    op.add_column(
        "asset_types",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_asset_types_project_id",
        "asset_types",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_asset_types_created_by_user_id",
        "asset_types",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_asset_types_project_id", "asset_types", ["project_id"])
    op.create_check_constraint(
        "ck_asset_types_scope",
        "asset_types",
        "scope IN ('global', 'project')",
    )
    op.create_check_constraint(
        "ck_asset_types_scope_project",
        "asset_types",
        "(scope = 'global' AND project_id IS NULL) OR (scope = 'project' AND project_id IS NOT NULL)",
    )
    op.create_index(
        "ux_asset_types_project_local_slug",
        "asset_types",
        ["project_id", "local_slug"],
        unique=True,
        postgresql_where=sa.text("scope = 'project'"),
    )

    op.add_column(
        "item_classifications",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_item_classifications_project_id",
        "item_classifications",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_item_classifications_project_id", "item_classifications", ["project_id"])
    op.execute("DROP INDEX IF EXISTS idx_item_classifications_active")
    op.execute(
        "CREATE UNIQUE INDEX idx_item_classifications_active_global "
        "ON item_classifications (item_id) "
        "WHERE is_active = TRUE AND project_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_item_classifications_active_project "
        "ON item_classifications (project_id, item_id) "
        "WHERE is_active = TRUE AND project_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_item_classifications_active_project")
    op.execute("DROP INDEX IF EXISTS idx_item_classifications_active_global")
    op.execute(
        "CREATE UNIQUE INDEX idx_item_classifications_active "
        "ON item_classifications (item_id) WHERE is_active = TRUE"
    )
    op.drop_index("ix_item_classifications_project_id", table_name="item_classifications")
    op.drop_constraint("fk_item_classifications_project_id", "item_classifications", type_="foreignkey")
    op.drop_column("item_classifications", "project_id")

    op.drop_index("ux_asset_types_project_local_slug", table_name="asset_types")
    op.drop_constraint("ck_asset_types_scope_project", "asset_types", type_="check")
    op.drop_constraint("ck_asset_types_scope", "asset_types", type_="check")
    op.drop_index("ix_asset_types_project_id", table_name="asset_types")
    op.drop_constraint("fk_asset_types_created_by_user_id", "asset_types", type_="foreignkey")
    op.drop_constraint("fk_asset_types_project_id", "asset_types", type_="foreignkey")
    op.drop_column("asset_types", "created_by_user_id")
    op.drop_column("asset_types", "local_slug")
    op.drop_column("asset_types", "project_id")
    op.drop_column("asset_types", "scope")
    op.drop_column("asset_types", "description")
