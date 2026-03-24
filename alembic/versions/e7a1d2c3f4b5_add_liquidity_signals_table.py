"""add liquidity signals table

Revision ID: e7a1d2c3f4b5
Revises: b2c7e8f9a1d4
Create Date: 2026-03-18 10:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e7a1d2c3f4b5"
down_revision: Union[str, None] = "b2c7e8f9a1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "liquidity_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("signal_type", sa.String(length=40), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("magnet_level", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("bias", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=64), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_liquidity_signals_dedup_key"),
    )
    op.create_index("ix_liquidity_signals_detected_at", "liquidity_signals", ["detected_at"], unique=False)
    op.create_index(
        "ix_liquidity_signals_symbol_timeframe_detected",
        "liquidity_signals",
        ["symbol", "timeframe", "detected_at"],
        unique=False,
    )
    op.create_index(
        "ix_liquidity_signals_symbol_type_detected",
        "liquidity_signals",
        ["symbol", "signal_type", "detected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_liquidity_signals_symbol_type_detected", table_name="liquidity_signals")
    op.drop_index("ix_liquidity_signals_symbol_timeframe_detected", table_name="liquidity_signals")
    op.drop_index("ix_liquidity_signals_detected_at", table_name="liquidity_signals")
    op.drop_table("liquidity_signals")
