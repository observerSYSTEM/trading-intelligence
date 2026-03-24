"""add mt5 candles table

Revision ID: a4f9d2c1b7e3
Revises: 194a00b0adab
Create Date: 2026-02-13 11:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4f9d2c1b7e3"
down_revision: Union[str, Sequence[str], None] = "194a00b0adab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_candles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "timeframe",
            "time_utc",
            name="uq_mt5_candles_symbol_tf_time",
        ),
    )
    op.create_index(
        "ix_mt5_candles_symbol_tf_time",
        "mt5_candles",
        ["symbol", "timeframe", "time_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mt5_candles_symbol_tf_time", table_name="mt5_candles")
    op.drop_table("mt5_candles")
