"""add_stored_files_and_site_plans

Revision ID: e3f5a7b9c1d2
Revises: d2e4f6a8b0c1
Create Date: 2026-02-25

Adds:
  - stored_files  : storage-backend-agnostic file record (local disk now, S3 later)
  - site_plans    : domain model for uploaded site plans, referencing stored_files
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3f5a7b9c1d2"
down_revision: Union[str, None] = "d2e4f6a8b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # stored_files
    # ------------------------------------------------------------------
    op.create_table(
        "stored_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column(
            "storage_backend",
            sa.String(50),
            nullable=False,
            server_default="local",
        ),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_stored_files_uploaded_by_id", "stored_files", ["uploaded_by_id"])

    # ------------------------------------------------------------------
    # site_plans
    # ------------------------------------------------------------------
    op.create_table(
        "site_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
    )
    op.create_index("ix_site_plans_project_id", "site_plans", ["project_id"])
    op.create_index("ix_site_plans_file_id", "site_plans", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_site_plans_file_id", table_name="site_plans")
    op.drop_index("ix_site_plans_project_id", table_name="site_plans")
    op.drop_table("site_plans")

    op.drop_index("ix_stored_files_uploaded_by_id", table_name="stored_files")
    op.drop_table("stored_files")
