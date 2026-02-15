"""add_multi_booking_slots

Add pending_booking_capacity column to assets table and create a partial
unique index on slot_bookings to enforce at most one active booking
(CONFIRMED / IN_PROGRESS / COMPLETED) per asset time slot.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-02-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pending_booking_capacity column with default 5
    op.add_column(
        'assets',
        sa.Column(
            'pending_booking_capacity',
            sa.Integer(),
            nullable=False,
            server_default='5',
        ),
    )

    # Partial unique index: at most one active booking per asset time slot.
    # Uses BookingStatus enum member NAMES (uppercase) because that is what
    # SQLAlchemy's SQLEnum(BookingStatus) stores in PostgreSQL.
    op.execute("""
        CREATE UNIQUE INDEX ix_one_active_per_slot
        ON slot_bookings (asset_id, booking_date, start_time, end_time)
        WHERE status IN ('CONFIRMED', 'IN_PROGRESS', 'COMPLETED')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_one_active_per_slot")
    op.drop_column('assets', 'pending_booking_capacity')
