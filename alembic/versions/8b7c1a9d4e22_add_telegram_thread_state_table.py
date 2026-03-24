"""add telegram thread state table

Revision ID: 8b7c1a9d4e22
Revises: 6f9b3d2e1a44
Create Date: 2026-02-15 23:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "8b7c1a9d4e22"
down_revision: Union[str, Sequence[str], None] = "6f9b3d2e1a44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_thread_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_uk", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("pinned_message_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date_uk", "symbol", "chat_id", name="uq_telegram_thread_state_date_symbol_chat"),
    )
    op.create_index("ix_telegram_thread_state_date_symbol", "telegram_thread_state", ["date_uk", "symbol"], unique=False)
    op.create_index("ix_telegram_thread_state_chat", "telegram_thread_state", ["chat_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_telegram_thread_state_chat", table_name="telegram_thread_state")
    op.drop_index("ix_telegram_thread_state_date_symbol", table_name="telegram_thread_state")
    op.drop_table("telegram_thread_state")

