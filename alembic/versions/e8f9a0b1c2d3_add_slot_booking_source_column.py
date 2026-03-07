"""add_slot_booking_source_column

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-03-07

Adds:
  - slot_bookings.source column
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.add_column("slot_bookings", sa.Column("source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("slot_bookings", "source")
