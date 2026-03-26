"""merge_heads

Revision ID: 98e517dc6261
Revises: d3e4f5a6b7c8, e5f6a7b8c9d0
Create Date: 2026-03-26 12:23:26.505354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98e517dc6261'
down_revision: Union[str, None] = ('d3e4f5a6b7c8', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
