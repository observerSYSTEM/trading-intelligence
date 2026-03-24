"""add oracle_targets_snapshot

Revision ID: c3d7b9a4f1e2
Revises: b8f4c2d9e6a1
Create Date: 2026-02-16 08:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c3d7b9a4f1e2"
down_revision = "b8f4c2d9e6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oracle_targets_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False, server_default="pro"),
        sa.Column("timeframe_base", sa.String(), nullable=False, server_default="H1"),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_bid", sa.Float(), nullable=True),
        sa.Column("price_ask", sa.Float(), nullable=True),
        sa.Column("magnet_price", sa.Float(), nullable=False),
        sa.Column("zone_to_zone_target", sa.Float(), nullable=False),
        sa.Column("sellside_liquidity", sa.Float(), nullable=False),
        sa.Column("buyside_liquidity", sa.Float(), nullable=False),
        sa.Column("magnet_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oracle_targets_snapshot_as_of_utc",
        "oracle_targets_snapshot",
        ["as_of_utc"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_targets_snapshot_symbol_tier_asof",
        "oracle_targets_snapshot",
        ["symbol", "tier", "as_of_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oracle_targets_snapshot_symbol_tier_asof", table_name="oracle_targets_snapshot")
    op.drop_index("ix_oracle_targets_snapshot_as_of_utc", table_name="oracle_targets_snapshot")
    op.drop_table("oracle_targets_snapshot")
