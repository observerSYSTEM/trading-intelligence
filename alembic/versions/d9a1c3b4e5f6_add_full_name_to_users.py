"""add full_name to users

Revision ID: d9a1c3b4e5f6
Revises: c6d3e1a4b9f2
Create Date: 2026-03-14 12:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9a1c3b4e5f6"
down_revision: Union[str, None] = "c6d3e1a4b9f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("full_name", sa.String(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("users", "full_name")

