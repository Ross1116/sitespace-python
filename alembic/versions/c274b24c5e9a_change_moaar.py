"""change moaar

Revision ID: c274b24c5e9a
Revises: 0adc3de1aada
Create Date: 2025-10-24 20:52:53.088990

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c274b24c5e9a'
down_revision: Union[str, None] = '0adc3de1aada'
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
