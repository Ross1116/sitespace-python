"""add_programme_uploads_and_activities

Revision ID: a1b2c3d4e5f6
Revises: e3f5a7b9c1d2
Create Date: 2026-03-07

Adds:
  - programme_uploads   : versioned programme file records with completeness tracking
  - programme_activities: hierarchical activity tree with self-ref parent_id (deferred FK)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e3f5a7b9c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # programme_uploads
    # ------------------------------------------------------------------
    op.create_table(
        "programme_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("completeness_score", sa.Float(), nullable=True),
        sa.Column("completeness_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # status values: "processing" | "committed"
        sa.Column("status", sa.String(20), nullable=False, server_default="processing"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_programme_uploads_project_id", "programme_uploads", ["project_id"])
    op.create_index("ix_programme_uploads_uploaded_by", "programme_uploads", ["uploaded_by"])
    op.create_index("ix_programme_uploads_status", "programme_uploads", ["status"])

    # ------------------------------------------------------------------
    # programme_activities
    # Self-referential parent_id requires DEFERRABLE INITIALLY DEFERRED
    # so bulk inserts don't fail when a child row is committed before its parent.
    # ------------------------------------------------------------------
    op.create_table(
        "programme_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "programme_upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programme_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # parent_id is a self-ref — constraint declared separately below with DEFERRABLE
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("level_name", sa.String(100), nullable=True),
        sa.Column("zone_name", sa.String(100), nullable=True),
        sa.Column("is_summary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("wbs_code", sa.String(100), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        # import_flags: list of strings e.g. ["dates_missing", "unstructured", "date_parse_failed"]
        sa.Column("import_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["programme_activities.id"],
            name="fk_programme_activities_parent_id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        "ix_programme_activities_upload_id",
        "programme_activities",
        ["programme_upload_id"],
    )
    op.create_index(
        "ix_programme_activities_parent_id",
        "programme_activities",
        ["parent_id"],
    )
    op.create_index(
        "ix_programme_activities_upload_sort",
        "programme_activities",
        ["programme_upload_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_programme_activities_upload_sort", table_name="programme_activities")
    op.drop_index("ix_programme_activities_parent_id", table_name="programme_activities")
    op.drop_index("ix_programme_activities_upload_id", table_name="programme_activities")
    op.drop_table("programme_activities")

    op.drop_index("ix_programme_uploads_status", table_name="programme_uploads")
    op.drop_index("ix_programme_uploads_uploaded_by", table_name="programme_uploads")
    op.drop_index("ix_programme_uploads_project_id", table_name="programme_uploads")
    op.drop_table("programme_uploads")
