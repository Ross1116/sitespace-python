"""enforce lowercase email storage

Revision ID: 1c2d3e4f5a6b
Revises: c5d6e7f8a9b0
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c2d3e4f5a6b'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET email = lower(trim(email)) WHERE email IS NOT NULL")
    op.execute("UPDATE subcontractors SET email = lower(trim(email)) WHERE email IS NOT NULL")

    op.create_check_constraint(
        "ck_users_email_lowercase",
        "users",
        "email = lower(email)"
    )
    op.create_check_constraint(
        "ck_subcontractors_email_lowercase",
        "subcontractors",
        "email = lower(email)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_subcontractors_email_lowercase", "subcontractors", type_="check")
    op.drop_constraint("ck_users_email_lowercase", "users", type_="check")
