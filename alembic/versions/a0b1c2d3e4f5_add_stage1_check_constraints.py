"""add_stage1_check_constraints

Revision ID: a0b1c2d3e4f5
Revises: f0a1b2c3d4e5
Create Date: 2026-03-24

Stage 1 — DB-level CHECK constraints for correctness columns.

Adds:
  programme_uploads.work_days_per_week     BETWEEN 1 AND 7
  programme_activities.pct_complete        BETWEEN 0 AND 100
  programme_activities.row_confidence      IN ('high','medium','low')
  programme_activities.activity_kind       IN ('summary','task','milestone')
"""

from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE programme_uploads "
        "ADD CONSTRAINT ck_programme_uploads_work_days "
        "CHECK (work_days_per_week BETWEEN 1 AND 7)"
    )
    op.execute(
        "ALTER TABLE programme_activities "
        "ADD CONSTRAINT ck_programme_activities_pct_complete "
        "CHECK (pct_complete BETWEEN 0 AND 100)"
    )
    op.execute(
        "ALTER TABLE programme_activities "
        "ADD CONSTRAINT ck_programme_activities_row_confidence "
        "CHECK (row_confidence IN ('high', 'medium', 'low'))"
    )
    op.execute(
        "ALTER TABLE programme_activities "
        "ADD CONSTRAINT ck_programme_activities_activity_kind "
        "CHECK (activity_kind IN ('summary', 'task', 'milestone'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE programme_activities DROP CONSTRAINT ck_programme_activities_activity_kind")
    op.execute("ALTER TABLE programme_activities DROP CONSTRAINT ck_programme_activities_row_confidence")
    op.execute("ALTER TABLE programme_activities DROP CONSTRAINT ck_programme_activities_pct_complete")
    op.execute("ALTER TABLE programme_uploads DROP CONSTRAINT ck_programme_uploads_work_days")
