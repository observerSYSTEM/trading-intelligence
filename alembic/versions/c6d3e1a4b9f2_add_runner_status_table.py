"""add runner status table

Revision ID: c6d3e1a4b9f2
Revises: b4c2d1e9f8a7
Create Date: 2026-02-19 09:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c6d3e1a4b9f2"
down_revision = "b4c2d1e9f8a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runner_status",
        sa.Column("runner_id", sa.String(), nullable=False),
        sa.Column("mt5_connected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_tick_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ingest_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("symbols_ok_json", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("runner_id"),
    )
    op.create_index("ix_runner_status_last_ok_at", "runner_status", ["last_ok_at"], unique=False)
    op.create_index("ix_runner_status_updated_at", "runner_status", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runner_status_updated_at", table_name="runner_status")
    op.drop_index("ix_runner_status_last_ok_at", table_name="runner_status")
    op.drop_table("runner_status")
