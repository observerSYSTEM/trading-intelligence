"""oracle threads, trades, and manipulation fields

Revision ID: 9a2c6f7d1b4a
Revises: e1a9c7d42f31
Create Date: 2026-02-14 23:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9a2c6f7d1b4a"
down_revision: Union[str, Sequence[str], None] = "e1a9c7d42f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "oracle_runs",
        sa.Column("manipulation_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "oracle_runs",
        sa.Column("manipulation_level", sa.String(), nullable=False, server_default="low"),
    )
    op.add_column(
        "notification_routes",
        sa.Column("telegram_pin_daily_bias", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "telegram_threads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False, server_default="XAUUSD"),
        sa.Column("date_uk", sa.Date(), nullable=False),
        sa.Column("anchor_message_id", sa.Integer(), nullable=False),
        sa.Column("update_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "symbol", "date_uk", name="uq_telegram_threads_user_symbol_date"),
    )
    op.create_index("ix_telegram_threads_user_id", "telegram_threads", ["user_id"], unique=False)
    op.create_index("ix_telegram_threads_date_uk", "telegram_threads", ["date_uk"], unique=False)

    op.create_table(
        "trades",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False, server_default="XAUUSD"),
        sa.Column("date_uk", sa.Date(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("sl", sa.Float(), nullable=True),
        sa.Column("tp1", sa.Float(), nullable=True),
        sa.Column("tp2", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("rr_realized", sa.Float(), nullable=True),
        sa.Column("reason_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_user_id", "trades", ["user_id"], unique=False)
    op.create_index("ix_trades_date_uk", "trades", ["date_uk"], unique=False)
    op.create_index("ix_trades_status", "trades", ["status"], unique=False)

    op.create_table(
        "trade_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trade_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_events_trade_id", "trade_events", ["trade_id"], unique=False)
    op.create_index("ix_trade_events_created_at", "trade_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trade_events_created_at", table_name="trade_events")
    op.drop_index("ix_trade_events_trade_id", table_name="trade_events")
    op.drop_table("trade_events")

    op.drop_index("ix_trades_status", table_name="trades")
    op.drop_index("ix_trades_date_uk", table_name="trades")
    op.drop_index("ix_trades_user_id", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_telegram_threads_date_uk", table_name="telegram_threads")
    op.drop_index("ix_telegram_threads_user_id", table_name="telegram_threads")
    op.drop_table("telegram_threads")

    op.drop_column("notification_routes", "telegram_pin_daily_bias")
    op.drop_column("oracle_runs", "manipulation_level")
    op.drop_column("oracle_runs", "manipulation_score")
