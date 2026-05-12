"""allow derived activity work profile source

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-05-12
"""

from typing import Sequence, Union

from alembic import op


revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_activity_work_profiles_source", "activity_work_profiles", type_="check")
    op.create_check_constraint(
        "ck_activity_work_profiles_source",
        "activity_work_profiles",
        "source IN ('ai', 'cache', 'manual', 'default', 'derived')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_activity_work_profiles_source", "activity_work_profiles", type_="check")
    op.create_check_constraint(
        "ck_activity_work_profiles_source",
        "activity_work_profiles",
        "source IN ('ai', 'cache', 'manual', 'default')",
    )
