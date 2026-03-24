"""add_stage1_correctness_columns

Revision ID: f0a1b2c3d4e5
Revises: e4f5a6b7c8d9
Create Date: 2026-03-24

Stage 1 — Parser hardening + correctness columns.

Adds:
  programme_uploads.work_days_per_week   SMALLINT NOT NULL DEFAULT 5
  programme_activities.pct_complete      SMALLINT nullable  (0–100)
  programme_activities.activity_kind     VARCHAR(20) nullable  ('summary'|'task'|'milestone')
  programme_activities.row_confidence    VARCHAR(10) nullable  ('high'|'medium'|'low')
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "programme_uploads",
        sa.Column(
            "work_days_per_week",
            sa.SmallInteger(),
            nullable=False,
            server_default="5",
        ),
    )

    op.add_column(
        "programme_activities",
        sa.Column("pct_complete", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "programme_activities",
        sa.Column("activity_kind", sa.String(20), nullable=True),
    )
    op.add_column(
        "programme_activities",
        sa.Column("row_confidence", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("programme_activities", "row_confidence")
    op.drop_column("programme_activities", "activity_kind")
    op.drop_column("programme_activities", "pct_complete")
    op.drop_column("programme_uploads", "work_days_per_week")
