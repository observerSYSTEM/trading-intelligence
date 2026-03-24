"""add last renewal reminder at to subscriptions

Revision ID: 8a4f1de90c2b
Revises: d4b9a7c3e2f1
Create Date: 2026-02-14 23:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8a4f1de90c2b"
down_revision: Union[str, Sequence[str], None] = "d4b9a7c3e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("last_renewal_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "last_renewal_reminder_at")
