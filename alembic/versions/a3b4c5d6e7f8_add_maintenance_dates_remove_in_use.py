"""add_maintenance_dates_remove_in_use

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-02-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Detach column from enum — no more validation on any operation
    op.execute("ALTER TABLE assets ALTER COLUMN status TYPE text")

    # 2. Normalize ALL values to lowercase and remap in_use → available
    op.execute("UPDATE assets SET status = LOWER(status)")
    op.execute(
        "UPDATE assets SET status = 'available' "
        "WHERE status = 'in_use'"
    )

    # 3. Replace the enum with new lowercase values (without in_use)
    op.execute("DROP TYPE assetstatus")
    op.execute(
        "CREATE TYPE assetstatus AS ENUM "
        "('available', 'deployed', 'retired', 'maintenance')"
    )

    # 4. Convert column back to the new enum
    op.execute(
        "ALTER TABLE assets ALTER COLUMN status "
        "TYPE assetstatus USING status::assetstatus"
    )

    # 5. Add maintenance date columns
    op.add_column('assets', sa.Column('maintenance_start_date', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('maintenance_end_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('assets', 'maintenance_end_date')
    op.drop_column('assets', 'maintenance_start_date')

    # Reverse the enum change
    op.execute("ALTER TABLE assets ALTER COLUMN status TYPE text")
    op.execute("DROP TYPE assetstatus")
    op.execute(
        "CREATE TYPE assetstatus AS ENUM "
        "('AVAILABLE', 'IN_USE', 'DEPLOYED', 'RETIRED', 'MAINTENANCE')"
    )
    op.execute("UPDATE assets SET status = UPPER(status)")
    op.execute(
        "ALTER TABLE assets ALTER COLUMN status "
        "TYPE assetstatus USING status::assetstatus"
    )