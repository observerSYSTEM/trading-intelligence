"""add security audit and webhook tables

Revision ID: d4b9a7c3e2f1
Revises: 7d1a4f2b8c90
Create Date: 2026-02-14 23:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4b9a7c3e2f1"
down_revision: Union[str, Sequence[str], None] = "7d1a4f2b8c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
    )
    op.create_index("ix_stripe_webhook_events_received_at", "stripe_webhook_events", ["received_at"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)
    op.create_index("ix_audit_logs_action_ts", "audit_logs", ["action", "ts"], unique=False)
    op.create_index("ix_audit_logs_ts", "audit_logs", ["ts"], unique=False)

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_ip_ts", "login_attempts", ["ip", "ts"], unique=False)
    op.create_index("ix_login_attempts_email_ts", "login_attempts", ["email", "ts"], unique=False)
    op.create_index("ix_login_attempts_ts", "login_attempts", ["ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_login_attempts_ts", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email_ts", table_name="login_attempts")
    op.drop_index("ix_login_attempts_ip_ts", table_name="login_attempts")
    op.drop_table("login_attempts")

    op.drop_index("ix_audit_logs_ts", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_ts", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_stripe_webhook_events_received_at", table_name="stripe_webhook_events")
    op.drop_table("stripe_webhook_events")
