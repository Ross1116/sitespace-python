"""make_subcontractor_optional_in_bookings

Revision ID: 9882e0a87665
Revises: 526656b03766
Create Date: 2025-11-01 14:50:30.064413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9882e0a87665'
down_revision: Union[str, None] = '526656b03766'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make subcontractor_id nullable in slot_bookings table"""
    op.alter_column('slot_bookings', 'subcontractor_id',
                    existing_type=postgresql.UUID(),
                    nullable=True)


def downgrade() -> None:
    """Revert subcontractor_id to non-nullable (WARNING: will fail if NULL values exist)"""
    # First, you might want to delete or update any bookings with NULL subcontractor_id
    # Uncomment the following lines if you want to handle existing NULL values:
    
    # Delete bookings with NULL subcontractor_id
    # op.execute("DELETE FROM slot_bookings WHERE subcontractor_id IS NULL")
    
    # OR set them to a default subcontractor (replace with actual UUID)
    # op.execute("UPDATE slot_bookings SET subcontractor_id = 'some-uuid-here' WHERE subcontractor_id IS NULL")
    
    op.alter_column('slot_bookings', 'subcontractor_id',
                    existing_type=postgresql.UUID(),
                    nullable=False)