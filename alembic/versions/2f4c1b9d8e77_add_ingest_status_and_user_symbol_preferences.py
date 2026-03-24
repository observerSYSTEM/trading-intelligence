"""add ingest status and user symbol preferences

Revision ID: 2f4c1b9d8e77
Revises: 9a2c6f7d1b4a
Create Date: 2026-02-15 00:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f4c1b9d8e77"
down_revision: Union[str, Sequence[str], None] = "9a2c6f7d1b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_ingest_status",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index("ix_mt5_ingest_status_last_ingested_at", "mt5_ingest_status", ["last_ingested_at"], unique=False)

    op.create_table(
        "user_symbol_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "symbol", name="uq_user_symbol_preferences_user_symbol"),
    )
    op.create_index("ix_user_symbol_preferences_user_id", "user_symbol_preferences", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_symbol_preferences_user_id", table_name="user_symbol_preferences")
    op.drop_table("user_symbol_preferences")

    op.drop_index("ix_mt5_ingest_status_last_ingested_at", table_name="mt5_ingest_status")
    op.drop_table("mt5_ingest_status")
