"""add account activation tokens

Revision ID: b2c7e8f9a1d4
Revises: d9a1c3b4e5f6
Create Date: 2026-03-14 15:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2c7e8f9a1d4"
down_revision: Union[str, None] = "d9a1c3b4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_activation_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_account_activation_tokens_token_hash"),
    )
    op.create_index("ix_account_activation_tokens_user_id", "account_activation_tokens", ["user_id"], unique=False)
    op.create_index("ix_account_activation_tokens_expires_at", "account_activation_tokens", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_account_activation_tokens_expires_at", table_name="account_activation_tokens")
    op.drop_index("ix_account_activation_tokens_user_id", table_name="account_activation_tokens")
    op.drop_table("account_activation_tokens")
