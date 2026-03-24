"""add user_signal_prefs and extend trade_events

Revision ID: b8f4c2d9e6a1
Revises: 9c2f7a8b1d4e
Create Date: 2026-02-16 05:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b8f4c2d9e6a1"
down_revision = "9c2f7a8b1d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_signal_prefs",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbols_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("telegram_chat_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.add_column("trade_events", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("trade_events", sa.Column("symbol", sa.String(), nullable=True))
    op.add_column("trade_events", sa.Column("tier_min", sa.String(), nullable=True))
    op.add_column("trade_events", sa.Column("title", sa.String(), nullable=True))
    op.add_column("trade_events", sa.Column("message", sa.String(), nullable=True))
    op.add_column("trade_events", sa.Column("meta_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_foreign_key("fk_trade_events_user_id_users", "trade_events", "users", ["user_id"], ["id"])
    op.create_index("ix_trade_events_user_id", "trade_events", ["user_id"], unique=False)
    op.create_index("ix_trade_events_symbol_created", "trade_events", ["symbol", "created_at"], unique=False)

    op.alter_column("trade_events", "trade_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("trade_events", "trade_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_trade_events_symbol_created", table_name="trade_events")
    op.drop_index("ix_trade_events_user_id", table_name="trade_events")
    op.drop_constraint("fk_trade_events_user_id_users", "trade_events", type_="foreignkey")
    op.drop_column("trade_events", "meta_json")
    op.drop_column("trade_events", "message")
    op.drop_column("trade_events", "title")
    op.drop_column("trade_events", "tier_min")
    op.drop_column("trade_events", "symbol")
    op.drop_column("trade_events", "user_id")

    op.drop_table("user_signal_prefs")
