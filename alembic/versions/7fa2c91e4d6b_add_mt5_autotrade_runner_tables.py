"""add mt5 autotrade runner tables

Revision ID: 7fa2c91e4d6b
Revises: 1d4a9c7e2b31
Create Date: 2026-02-16 04:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7fa2c91e4d6b"
down_revision: Union[str, Sequence[str], None] = "1d4a9c7e2b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("autotrade_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "user_symbol_preferences",
        sa.Column("autotrade_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "autotrade_global_control",
        sa.Column("id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("autotrade_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO autotrade_global_control (id, autotrade_enabled) VALUES (1, false) ON CONFLICT (id) DO NOTHING")

    op.create_table(
        "trade_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False, server_default="MARKET"),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("sl", sa.Float(), nullable=True),
        sa.Column("tp", sa.Float(), nullable=True),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("broker_runner_id", sa.String(), nullable=True),
        sa.Column("sent_to_runner_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["oracle_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_jobs_user_id", "trade_jobs", ["user_id"], unique=False)
    op.create_index("ix_trade_jobs_run_id", "trade_jobs", ["run_id"], unique=False)
    op.create_index("ix_trade_jobs_status_created", "trade_jobs", ["status", "created_at"], unique=False)
    op.create_index("ix_trade_jobs_user_symbol", "trade_jobs", ["user_id", "symbol"], unique=False)

    op.create_table(
        "trade_exec",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_ticket", sa.String(), nullable=True),
        sa.Column("filled_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["trade_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_trade_exec_job_id"),
    )
    op.create_index("ix_trade_exec_status", "trade_exec", ["status"], unique=False)
    op.create_index("ix_trade_exec_broker_ticket", "trade_exec", ["broker_ticket"], unique=False)

    op.create_table(
        "position_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ticket", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("sl", sa.Float(), nullable=True),
        sa.Column("tp", sa.Float(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ticket", name="uq_position_state_user_ticket"),
    )
    op.create_index("ix_position_state_user_id", "position_state", ["user_id"], unique=False)
    op.create_index("ix_position_state_user_symbol", "position_state", ["user_id", "symbol"], unique=False)
    op.create_index("ix_position_state_updated_at", "position_state", ["updated_at"], unique=False)

    op.create_table(
        "runner_heartbeats",
        sa.Column("runner_id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("symbols_enabled_json", sa.JSON(), nullable=False),
        sa.Column("last_ip", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("runner_id"),
    )


def downgrade() -> None:
    op.drop_table("runner_heartbeats")

    op.drop_index("ix_position_state_updated_at", table_name="position_state")
    op.drop_index("ix_position_state_user_symbol", table_name="position_state")
    op.drop_index("ix_position_state_user_id", table_name="position_state")
    op.drop_table("position_state")

    op.drop_index("ix_trade_exec_broker_ticket", table_name="trade_exec")
    op.drop_index("ix_trade_exec_status", table_name="trade_exec")
    op.drop_table("trade_exec")

    op.drop_index("ix_trade_jobs_user_symbol", table_name="trade_jobs")
    op.drop_index("ix_trade_jobs_status_created", table_name="trade_jobs")
    op.drop_index("ix_trade_jobs_run_id", table_name="trade_jobs")
    op.drop_index("ix_trade_jobs_user_id", table_name="trade_jobs")
    op.drop_table("trade_jobs")

    op.drop_table("autotrade_global_control")

    op.drop_column("user_symbol_preferences", "autotrade_enabled")
    op.drop_column("subscriptions", "autotrade_enabled")

