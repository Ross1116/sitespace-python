"""removed sub user relation

Revision ID: ac2d91bc1a20
Revises: ec1ed26152c5
Create Date: 2025-10-24 23:03:21.816903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac2d91bc1a20'
down_revision: Union[str, None] = 'ec1ed26152c5'
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
