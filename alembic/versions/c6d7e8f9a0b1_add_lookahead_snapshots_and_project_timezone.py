"""add_lookahead_snapshots_and_project_timezone

Revision ID: c6d7e8f9a0b1
Revises: b2c3d4e5f6a7
Create Date: 2026-03-07

Adds:
  - site_projects.timezone (default Australia/Adelaide)
  - lookahead_snapshots table
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_projects",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Australia/Adelaide",
        ),
    )

    op.create_table(
        "lookahead_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "programme_upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programme_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("anomaly_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_lookahead_snapshots_project_id", "lookahead_snapshots", ["project_id"])
    op.create_index("ix_lookahead_snapshots_snapshot_date", "lookahead_snapshots", ["snapshot_date"])


def downgrade() -> None:
    op.drop_index("ix_lookahead_snapshots_snapshot_date", table_name="lookahead_snapshots")
    op.drop_index("ix_lookahead_snapshots_project_id", table_name="lookahead_snapshots")
    op.drop_table("lookahead_snapshots")
    op.drop_column("site_projects", "timezone")
