"""harden gold intel schema and route split

Revision ID: 3587f7ab9d59
Revises: 82f588700b16
Create Date: 2026-02-12 02:49:07.993227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3587f7ab9d59'
down_revision: Union[str, Sequence[str], None] = '82f588700b16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "gold_positioning_snapshot",
        sa.Column("public_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "gold_positioning_snapshot",
        sa.Column("internal_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.execute(
        "UPDATE gold_positioning_snapshot "
        "SET internal_factors_json = COALESCE(factors_json, '{}'::json), "
        "public_factors_json = '{}'::json"
    )
    op.alter_column("gold_positioning_snapshot", "public_factors_json", server_default=None)
    op.alter_column("gold_positioning_snapshot", "internal_factors_json", server_default=None)
    op.drop_index(op.f("ix_gold_positioning_snapshot_as_of_utc"), table_name="gold_positioning_snapshot")
    op.create_index(
        "ix_gold_positioning_snapshot_symbol_as_of",
        "gold_positioning_snapshot",
        ["symbol", "as_of_utc"],
        unique=False,
    )
    op.execute(
        """
        DELETE FROM gold_positioning_snapshot t
        USING (
            SELECT ctid
            FROM (
                SELECT
                    ctid,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, as_of_utc
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM gold_positioning_snapshot
            ) s
            WHERE s.rn > 1
        ) d
        WHERE t.ctid = d.ctid
        """
    )
    op.create_unique_constraint(
        "uq_gold_positioning_snapshot_symbol_as_of",
        "gold_positioning_snapshot",
        ["symbol", "as_of_utc"],
    )
    op.drop_column("gold_positioning_snapshot", "factors_json")

    op.add_column(
        "gold_regime_daily",
        sa.Column("public_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "gold_regime_daily",
        sa.Column("internal_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.execute(
        "UPDATE gold_regime_daily "
        "SET internal_factors_json = COALESCE(factors_json, '{}'::json), "
        "public_factors_json = '{}'::json"
    )
    op.alter_column("gold_regime_daily", "public_factors_json", server_default=None)
    op.alter_column("gold_regime_daily", "internal_factors_json", server_default=None)
    op.drop_index(op.f("ix_gold_regime_daily_as_of_utc"), table_name="gold_regime_daily")
    op.create_index(
        "ix_gold_regime_daily_symbol_as_of",
        "gold_regime_daily",
        ["symbol", "as_of_utc"],
        unique=False,
    )
    op.execute(
        """
        DELETE FROM gold_regime_daily t
        USING (
            SELECT ctid
            FROM (
                SELECT
                    ctid,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, as_of_utc
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM gold_regime_daily
            ) s
            WHERE s.rn > 1
        ) d
        WHERE t.ctid = d.ctid
        """
    )
    op.create_unique_constraint(
        "uq_gold_regime_daily_symbol_as_of",
        "gold_regime_daily",
        ["symbol", "as_of_utc"],
    )
    op.drop_column("gold_regime_daily", "factors_json")

    op.add_column(
        "gold_stress_intraday",
        sa.Column("public_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "gold_stress_intraday",
        sa.Column("internal_factors_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.execute(
        "UPDATE gold_stress_intraday "
        "SET internal_factors_json = COALESCE(factors_json, '{}'::json), "
        "public_factors_json = '{}'::json"
    )
    op.alter_column("gold_stress_intraday", "public_factors_json", server_default=None)
    op.alter_column("gold_stress_intraday", "internal_factors_json", server_default=None)
    op.drop_index(op.f("ix_gold_stress_intraday_as_of_utc"), table_name="gold_stress_intraday")
    op.create_index(
        "ix_gold_stress_intraday_symbol_as_of",
        "gold_stress_intraday",
        ["symbol", "as_of_utc"],
        unique=False,
    )
    op.execute(
        """
        DELETE FROM gold_stress_intraday t
        USING (
            SELECT ctid
            FROM (
                SELECT
                    ctid,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, as_of_utc
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM gold_stress_intraday
            ) s
            WHERE s.rn > 1
        ) d
        WHERE t.ctid = d.ctid
        """
    )
    op.create_unique_constraint(
        "uq_gold_stress_intraday_symbol_as_of",
        "gold_stress_intraday",
        ["symbol", "as_of_utc"],
    )
    op.drop_column("gold_stress_intraday", "factors_json")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "gold_stress_intraday",
        sa.Column(
            "factors_json",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        "UPDATE gold_stress_intraday "
        "SET factors_json = COALESCE(internal_factors_json, '{}'::json)"
    )
    op.alter_column("gold_stress_intraday", "factors_json", server_default=None)
    op.drop_constraint("uq_gold_stress_intraday_symbol_as_of", "gold_stress_intraday", type_="unique")
    op.drop_index("ix_gold_stress_intraday_symbol_as_of", table_name="gold_stress_intraday")
    op.create_index(
        op.f("ix_gold_stress_intraday_as_of_utc"),
        "gold_stress_intraday",
        ["as_of_utc"],
        unique=False,
    )
    op.drop_column("gold_stress_intraday", "internal_factors_json")
    op.drop_column("gold_stress_intraday", "public_factors_json")

    op.add_column(
        "gold_regime_daily",
        sa.Column(
            "factors_json",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        "UPDATE gold_regime_daily "
        "SET factors_json = COALESCE(internal_factors_json, '{}'::json)"
    )
    op.alter_column("gold_regime_daily", "factors_json", server_default=None)
    op.drop_constraint("uq_gold_regime_daily_symbol_as_of", "gold_regime_daily", type_="unique")
    op.drop_index("ix_gold_regime_daily_symbol_as_of", table_name="gold_regime_daily")
    op.create_index(op.f("ix_gold_regime_daily_as_of_utc"), "gold_regime_daily", ["as_of_utc"], unique=False)
    op.drop_column("gold_regime_daily", "internal_factors_json")
    op.drop_column("gold_regime_daily", "public_factors_json")

    op.add_column(
        "gold_positioning_snapshot",
        sa.Column(
            "factors_json",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        "UPDATE gold_positioning_snapshot "
        "SET factors_json = COALESCE(internal_factors_json, '{}'::json)"
    )
    op.alter_column("gold_positioning_snapshot", "factors_json", server_default=None)
    op.drop_constraint(
        "uq_gold_positioning_snapshot_symbol_as_of",
        "gold_positioning_snapshot",
        type_="unique",
    )
    op.drop_index("ix_gold_positioning_snapshot_symbol_as_of", table_name="gold_positioning_snapshot")
    op.create_index(
        op.f("ix_gold_positioning_snapshot_as_of_utc"),
        "gold_positioning_snapshot",
        ["as_of_utc"],
        unique=False,
    )
    op.drop_column("gold_positioning_snapshot", "internal_factors_json")
    op.drop_column("gold_positioning_snapshot", "public_factors_json")
