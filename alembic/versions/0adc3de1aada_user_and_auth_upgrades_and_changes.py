"""user and auth upgrades and changes

Revision ID: 0adc3de1aada
Revises: a1211171ba45
Create Date: 2025-10-24 14:12:28.721019

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0adc3de1aada'
down_revision: Union[str, None] = 'a1211171ba45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This migration was intentionally left as a no-op.
    # The schema changes described by this revision were either applied
    # directly to the database or superseded by a subsequent migration.
    # The revision is kept in the chain to preserve history.
    pass


def downgrade() -> None:
    # This migration was intentionally left as a no-op.
    # The schema changes described by this revision were either applied
    # directly to the database or superseded by a subsequent migration.
    # The revision is kept in the chain to preserve history.
    pass
