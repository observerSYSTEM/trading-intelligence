"""add quarterly permission tables

Revision ID: 7d1a4f2b8c90
Revises: 2f4c1b9d8e77
Create Date: 2026-02-14 22:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d1a4f2b8c90"
down_revision: Union[str, Sequence[str], None] = "2f4c1b9d8e77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oracle_quarterly_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("quarter_key", sa.String(), nullable=False),
        sa.Column("quarter_open", sa.Float(), nullable=False),
        sa.Column("q_high", sa.Float(), nullable=False),
        sa.Column("q_low", sa.Float(), nullable=False),
        sa.Column("q_mid", sa.Float(), nullable=False),
        sa.Column("premium_discount", sa.String(), nullable=False),
        sa.Column("quarterly_bias", sa.String(), nullable=False),
        sa.Column("permission_mode", sa.String(), nullable=False),
        sa.Column("conflict_rule", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("factors_json", sa.JSON(), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "quarter_key", name="uq_oracle_quarterly_snapshots_symbol_quarter"),
    )
    op.create_index(
        "ix_oracle_quarterly_snapshots_symbol_quarter",
        "oracle_quarterly_snapshots",
        ["symbol", "quarter_key"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_quarterly_snapshots_symbol_asof",
        "oracle_quarterly_snapshots",
        ["symbol", "as_of_utc"],
        unique=False,
    )

    op.create_table(
        "oracle_permission_daily",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("date_uk", sa.Date(), nullable=False),
        sa.Column("daily_bias_raw", sa.String(), nullable=False),
        sa.Column("quarterly_bias", sa.String(), nullable=False),
        sa.Column("allowed_direction_final", sa.String(), nullable=False),
        sa.Column("alignment", sa.String(), nullable=False),
        sa.Column("confidence_final", sa.Float(), nullable=False),
        sa.Column("message_tag", sa.String(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date_uk", name="uq_oracle_permission_daily_symbol_date"),
    )
    op.create_index(
        "ix_oracle_permission_daily_symbol_date",
        "oracle_permission_daily",
        ["symbol", "date_uk"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_permission_daily_symbol_asof",
        "oracle_permission_daily",
        ["symbol", "as_of_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oracle_permission_daily_symbol_asof", table_name="oracle_permission_daily")
    op.drop_index("ix_oracle_permission_daily_symbol_date", table_name="oracle_permission_daily")
    op.drop_table("oracle_permission_daily")

    op.drop_index("ix_oracle_quarterly_snapshots_symbol_asof", table_name="oracle_quarterly_snapshots")
    op.drop_index("ix_oracle_quarterly_snapshots_symbol_quarter", table_name="oracle_quarterly_snapshots")
    op.drop_table("oracle_quarterly_snapshots")
