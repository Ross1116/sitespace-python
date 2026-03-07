"""add_unique_project_snapshot_date_to_lookahead_snapshots

Revision ID: b0c1d2e3f4a5
Revises: a9b8c7d6e5f4
Create Date: 2026-03-07

Adds:
  - unique constraint on lookahead_snapshots(project_id, snapshot_date)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_lookahead_snapshots_project_date",
        "lookahead_snapshots",
        ["project_id", "snapshot_date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_lookahead_snapshots_project_date",
        "lookahead_snapshots",
        type_="unique",
    )
