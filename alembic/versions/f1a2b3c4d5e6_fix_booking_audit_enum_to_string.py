"""fix_booking_audit_enum_to_string

Revision ID: f1a2b3c4d5e6
Revises: 9882e0a87665
Create Date: 2026-02-10 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '9882e0a87665'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop and recreate booking_audit_logs with String columns instead of enum types.
    # No data loss — the table was non-functional (inserts always failed due to enum mismatch).
    op.execute("DROP TABLE IF EXISTS booking_audit_logs CASCADE")

    # Drop orphaned enum types that were created for this table
    op.execute("DROP TYPE IF EXISTS bookingauditaction CASCADE")
    # Don't drop userrole — it may still be referenced elsewhere

    op.create_table(
        'booking_audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('slot_bookings.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('actor_role', sa.String(), nullable=False),
        sa.Column('actor_name', sa.Text(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),
        sa.Column('to_status', sa.String(), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_index('ix_audit_actor_created', 'booking_audit_logs',
                    ['actor_id', 'created_at'])


def downgrade() -> None:
    op.drop_table('booking_audit_logs')
