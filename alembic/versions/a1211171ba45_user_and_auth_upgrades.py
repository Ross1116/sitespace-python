"""user and auth upgrades

Revision ID: a1211171ba45
Revises: 9f973e2f9225
Create Date: 2025-10-24 13:45:35.721796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1211171ba45'
down_revision: Union[str, None] = '9f973e2f9225'
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
