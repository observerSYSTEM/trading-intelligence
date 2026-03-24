"""add daily permission snapshots and oracle magnet state

Revision ID: e5f1c2d9a7b4
Revises: c3d7b9a4f1e2
Create Date: 2026-02-16 22:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e5f1c2d9a7b4"
down_revision = "c3d7b9a4f1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_permission_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("date_uk", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False, server_default="M1"),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("daily_permission", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("spread", sa.Float(), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("is_extreme", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date_uk", name="uq_daily_permission_symbol_date"),
    )
    op.create_index(
        "ix_daily_permission_symbol_date",
        "daily_permission_snapshots",
        ["symbol", "date_uk"],
        unique=False,
    )
    op.create_index(
        "ix_daily_permission_symbol_asof",
        "daily_permission_snapshots",
        ["symbol", "as_of_utc"],
        unique=False,
    )

    op.create_table(
        "oracle_magnet_state",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe_base", sa.String(), nullable=False, server_default="H1"),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("magnet_price", sa.Float(), nullable=False),
        sa.Column("magnet_side", sa.String(), nullable=False),
        sa.Column("zone_to_zone_target", sa.Float(), nullable=False),
        sa.Column("sellside_liquidity", sa.Float(), nullable=False),
        sa.Column("buyside_liquidity", sa.Float(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index(
        "ix_oracle_magnet_state_asof",
        "oracle_magnet_state",
        ["as_of_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oracle_magnet_state_asof", table_name="oracle_magnet_state")
    op.drop_table("oracle_magnet_state")

    op.drop_index("ix_daily_permission_symbol_asof", table_name="daily_permission_snapshots")
    op.drop_index("ix_daily_permission_symbol_date", table_name="daily_permission_snapshots")
    op.drop_table("daily_permission_snapshots")

