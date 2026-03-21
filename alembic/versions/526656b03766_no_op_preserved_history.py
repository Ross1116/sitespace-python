"""no-op migration preserved for revision chain continuity

Revision ID: 526656b03766
Revises: ac2d91bc1a20
Create Date: 2025-10-25 01:00:55.424781

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '526656b03766'
down_revision: Union[str, None] = 'ac2d91bc1a20'
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
