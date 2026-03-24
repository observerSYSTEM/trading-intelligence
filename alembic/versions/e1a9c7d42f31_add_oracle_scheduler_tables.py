"""add oracle scheduler tables

Revision ID: e1a9c7d42f31
Revises: cf3d9b8a10f4
Create Date: 2026-02-14 18:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1a9c7d42f31"
down_revision: Union[str, Sequence[str], None] = "cf3d9b8a10f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oracle_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False, server_default="XAUUSD"),
        sa.Column("timeframe", sa.String(), nullable=False, server_default="H1"),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bias", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("internal_json", sa.JSON(), nullable=False),
        sa.Column("public_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oracle_runs_as_of_utc", "oracle_runs", ["as_of_utc"], unique=False)

    op.create_table(
        "oracle_confirmations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirm_ok", sa.Boolean(), nullable=False),
        sa.Column("confirm_reason_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["oracle_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oracle_confirmations_run_id", "oracle_confirmations", ["run_id"], unique=False)
    op.create_index("ix_oracle_confirmations_as_of_utc", "oracle_confirmations", ["as_of_utc"], unique=False)

    op.create_table(
        "signal_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False, server_default="telegram"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_text", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["oracle_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "run_id", name="uq_signal_deliveries_user_run"),
    )
    op.create_index("ix_signal_deliveries_user_id", "signal_deliveries", ["user_id"], unique=False)
    op.create_index("ix_signal_deliveries_run_id", "signal_deliveries", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_signal_deliveries_run_id", table_name="signal_deliveries")
    op.drop_index("ix_signal_deliveries_user_id", table_name="signal_deliveries")
    op.drop_table("signal_deliveries")

    op.drop_index("ix_oracle_confirmations_as_of_utc", table_name="oracle_confirmations")
    op.drop_index("ix_oracle_confirmations_run_id", table_name="oracle_confirmations")
    op.drop_table("oracle_confirmations")

    op.drop_index("ix_oracle_runs_as_of_utc", table_name="oracle_runs")
    op.drop_table("oracle_runs")
