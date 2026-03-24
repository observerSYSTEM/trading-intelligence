"""add weekly range snapshots table

Revision ID: 6f9b3d2e1a44
Revises: c2f4a91d6b8e
Create Date: 2026-02-15 23:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "6f9b3d2e1a44"
down_revision: Union[str, Sequence[str], None] = "c2f4a91d6b8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weekly_range_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("week_key", sa.String(), nullable=False),
        sa.Column("week_start_uk", sa.Date(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("mid", sa.Float(), nullable=False),
        sa.Column("range_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "week_key", name="uq_weekly_range_snapshots_symbol_week"),
    )
    op.create_index("ix_weekly_range_snapshots_symbol_week", "weekly_range_snapshots", ["symbol", "week_key"], unique=False)
    op.create_index("ix_weekly_range_snapshots_symbol_asof", "weekly_range_snapshots", ["symbol", "as_of_utc"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_weekly_range_snapshots_symbol_asof", table_name="weekly_range_snapshots")
    op.drop_index("ix_weekly_range_snapshots_symbol_week", table_name="weekly_range_snapshots")
    op.drop_table("weekly_range_snapshots")

