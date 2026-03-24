"""add user risk settings and audit events

Revision ID: 9c2f7a8b1d4e
Revises: 7fa2c91e4d6b
Create Date: 2026-02-16 05:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9c2f7a8b1d4e"
down_revision: Union[str, Sequence[str], None] = "7fa2c91e4d6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_risk_settings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_mode", sa.String(), nullable=False, server_default="fixed"),
        sa.Column("risk_value", sa.Float(), nullable=False, server_default=sa.text("0.01")),
        sa.Column("max_trades_day", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("max_daily_loss", sa.Float(), nullable=False, server_default=sa.text("3.0")),
        sa.Column("max_open_trades", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("max_lot", sa.Float(), nullable=False, server_default=sa.text("0.10")),
        sa.Column("allowed_symbols_json", sa.JSON(), nullable=False),
        sa.Column("avoid_mondays", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("block_on_volume_spike", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("news_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("news_block_minutes", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "autotrade_symbol_control",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("autotrade_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index(
        "ix_autotrade_symbol_control_enabled",
        "autotrade_symbol_control",
        ["autotrade_enabled"],
        unique=False,
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"], unique=False)
    op.create_index("ix_audit_events_user_created", "audit_events", ["user_id", "created_at"], unique=False)
    op.create_index("ix_audit_events_action_created", "audit_events", ["action", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_events_action_created", table_name="audit_events")
    op.drop_index("ix_audit_events_user_created", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_autotrade_symbol_control_enabled", table_name="autotrade_symbol_control")
    op.drop_table("autotrade_symbol_control")

    op.drop_table("user_risk_settings")

