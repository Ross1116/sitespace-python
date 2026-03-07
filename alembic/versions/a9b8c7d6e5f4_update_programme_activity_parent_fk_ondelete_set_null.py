"""update_programme_activity_parent_fk_ondelete_set_null

Revision ID: a9b8c7d6e5f4
Revises: f9a0b1c2d3e4
Create Date: 2026-03-07

Updates the self-referential parent FK on programme_activities to set child
parent_id to NULL when a parent row is deleted.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_programme_activities_parent_id",
        "programme_activities",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_programme_activities_parent_id",
        "programme_activities",
        "programme_activities",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_programme_activities_parent_id",
        "programme_activities",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_programme_activities_parent_id",
        "programme_activities",
        "programme_activities",
        ["parent_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
