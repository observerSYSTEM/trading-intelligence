"""add oracle processing state table

Revision ID: a1d9e4c7b2f3
Revises: f2b1c7e9d3a4
Create Date: 2026-02-18 08:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a1d9e4c7b2f3"
down_revision = "f2b1c7e9d3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oracle_processing_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("last_processed_candle_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_compute_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "timeframe", name="uq_oracle_processing_state_symbol_timeframe"),
    )
    op.create_index(
        "ix_oracle_processing_state_last_processed",
        "oracle_processing_state",
        ["last_processed_candle_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oracle_processing_state_last_processed", table_name="oracle_processing_state")
    op.drop_table("oracle_processing_state")

