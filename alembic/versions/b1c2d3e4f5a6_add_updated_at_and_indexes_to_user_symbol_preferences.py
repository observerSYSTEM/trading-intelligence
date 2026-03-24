"""add updated_at and indexes to user symbol preferences

Revision ID: b1c2d3e4f5a6
Revises: 8a4f1de90c2b
Create Date: 2026-02-15 10:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "8a4f1de90c2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_symbol_preferences",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_user_symbol_preferences_symbol",
        "user_symbol_preferences",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "ix_user_symbol_preferences_user_enabled",
        "user_symbol_preferences",
        ["user_id", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_symbol_preferences_user_enabled", table_name="user_symbol_preferences")
    op.drop_index("ix_user_symbol_preferences_symbol", table_name="user_symbol_preferences")
    op.drop_column("user_symbol_preferences", "updated_at")
