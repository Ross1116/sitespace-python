"""add_maintenance_dates_remove_in_use

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-02-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrate any existing in_use assets to available.
    #
    # PostgreSQL validates every literal in a query against the enum type
    # at *parse time* — before it checks whether any matching rows exist.
    # If 'in_use' is not a member of the assetstatus enum, the query is
    # rejected outright.  Casting the column to text bypasses that check
    # and lets us safely match on values that may or may not be in the enum.
    op.execute(
        "UPDATE assets SET status = 'available' "
        "WHERE status::text IN ('IN_USE', 'in_use')"
    )

    # Now remove the old value from the enum itself.
    # PostgreSQL has no ALTER TYPE ... DROP VALUE, so we recreate the type.
    op.execute("ALTER TYPE assetstatus RENAME TO assetstatus_old")
    op.execute(
        "CREATE TYPE assetstatus AS ENUM ("
        "  'available', 'deployed', 'maintenance', 'retired'"
        ")"
    )
    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status TYPE assetstatus "
        "USING status::text::assetstatus"
    )
    op.execute("DROP TYPE assetstatus_old")

    # Add maintenance date columns
    op.add_column('assets', sa.Column('maintenance_start_date', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('maintenance_end_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('assets', 'maintenance_end_date')
    op.drop_column('assets', 'maintenance_start_date')

    # Restore the old enum value
    op.execute("ALTER TYPE assetstatus ADD VALUE IF NOT EXISTS 'in_use'")