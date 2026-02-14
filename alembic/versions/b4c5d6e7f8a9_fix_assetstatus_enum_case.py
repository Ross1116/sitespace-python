"""fix_assetstatus_enum_case

Corrective migration: the deployed version of a3b4c5d6e7f8 accidentally
recreated the PostgreSQL ``assetstatus`` enum type with **lowercase**
values (``'available'``, ``'maintenance'``, …) and an extra ``'deployed'``
member.  SQLAlchemy's ``SQLEnum(AssetStatus)`` maps to enum member
**names** (uppercase), so every query now fails with:

    LookupError: 'available' is not among the defined enum values.
    Possible values: AVAILABLE, MAINTENANCE, RETIRED

This migration converts the column to VARCHAR, uppercases all data,
drops the broken enum type, recreates it with the correct uppercase
values, and converts the column back.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-02-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the default so we can alter the column type freely
    op.execute("ALTER TABLE assets ALTER COLUMN status DROP DEFAULT")

    # 2. Convert column from enum to plain VARCHAR
    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status TYPE VARCHAR(20) USING status::text"
    )

    # 3. Normalise data: uppercase everything and map invalid values
    op.execute("UPDATE assets SET status = UPPER(status)")
    op.execute(
        "UPDATE assets SET status = 'AVAILABLE' "
        "WHERE status NOT IN ('AVAILABLE', 'MAINTENANCE', 'RETIRED')"
    )

    # 4. Drop the broken enum type (may not exist on fresh DBs — use IF EXISTS)
    op.execute("DROP TYPE IF EXISTS assetstatus")

    # 5. Recreate with correct uppercase values
    op.execute(
        "CREATE TYPE assetstatus AS ENUM "
        "('AVAILABLE', 'MAINTENANCE', 'RETIRED')"
    )

    # 6. Convert the column back to the enum type
    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status TYPE assetstatus USING status::assetstatus"
    )

    # 7. Restore the default
    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status SET DEFAULT 'AVAILABLE'"
    )


def downgrade() -> None:
    # Reverse: convert back to lowercase enum values
    op.execute("ALTER TABLE assets ALTER COLUMN status DROP DEFAULT")

    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status TYPE VARCHAR(20) USING status::text"
    )

    op.execute("UPDATE assets SET status = LOWER(status)")

    op.execute("DROP TYPE IF EXISTS assetstatus")

    op.execute(
        "CREATE TYPE assetstatus AS ENUM "
        "('available', 'maintenance', 'retired')"
    )

    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status TYPE assetstatus USING status::assetstatus"
    )

    op.execute(
        "ALTER TABLE assets "
        "ALTER COLUMN status SET DEFAULT 'available'"
    )
