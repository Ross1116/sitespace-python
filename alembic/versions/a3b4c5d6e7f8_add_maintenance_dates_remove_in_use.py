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
    # Migrate any existing in_use assets to available
    op.execute("UPDATE assets SET status = 'available' WHERE status IN ('IN_USE', 'in_use')")

    # Add maintenance date columns
    op.add_column('assets', sa.Column('maintenance_start_date', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('maintenance_end_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('assets', 'maintenance_end_date')
    op.drop_column('assets', 'maintenance_start_date')
