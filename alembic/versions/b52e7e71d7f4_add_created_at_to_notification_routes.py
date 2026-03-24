"""add created_at to notification routes

Revision ID: b52e7e71d7f4
Revises: a4f9d2c1b7e3
Create Date: 2026-02-13 12:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b52e7e71d7f4"
down_revision: Union[str, Sequence[str], None] = "a4f9d2c1b7e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_routes",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_routes", "created_at")
