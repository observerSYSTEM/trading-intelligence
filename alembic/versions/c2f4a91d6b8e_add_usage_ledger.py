"""add usage ledger table

Revision ID: c2f4a91d6b8e
Revises: b1c2d3e4f5a6
Create Date: 2026-02-15 19:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c2f4a91d6b8e"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "signal_id", name="uq_usage_ledger_user_signal"),
    )
    op.create_index("ix_usage_ledger_user_id", "usage_ledger", ["user_id"], unique=False)
    op.create_index("ix_usage_ledger_user_created", "usage_ledger", ["user_id", "created_at"], unique=False)
    op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_usage_ledger_created_at", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_user_created", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_user_id", table_name="usage_ledger")
    op.drop_table("usage_ledger")

