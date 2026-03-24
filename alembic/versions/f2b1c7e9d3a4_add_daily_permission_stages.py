"""add prelim/official stage fields to daily permission snapshots

Revision ID: f2b1c7e9d3a4
Revises: e5f1c2d9a7b4
Create Date: 2026-02-17 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2b1c7e9d3a4"
down_revision = "e5f1c2d9a7b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("for_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("computed_at_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("daily_permission_stage", sa.String(), nullable=False, server_default="OFFICIAL"),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("permission_source", sa.String(), nullable=False, server_default="LONDON_0801"),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("official", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "daily_permission_snapshots",
        sa.Column("reasons_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    op.execute("UPDATE daily_permission_snapshots SET for_date = date_uk WHERE for_date IS NULL")
    op.execute("UPDATE daily_permission_snapshots SET computed_at_utc = as_of_utc WHERE computed_at_utc IS NULL")
    op.execute("UPDATE daily_permission_snapshots SET official = true WHERE daily_permission_stage = 'OFFICIAL'")
    op.execute(
        "UPDATE daily_permission_snapshots SET reasons_json = json_build_array(reason) "
        "WHERE reason IS NOT NULL AND (reasons_json IS NULL OR json_typeof(reasons_json) <> 'array')"
    )

    op.alter_column("daily_permission_snapshots", "for_date", nullable=False)
    op.alter_column("daily_permission_snapshots", "computed_at_utc", nullable=False)

    op.drop_constraint("uq_daily_permission_symbol_date", "daily_permission_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_daily_permission_symbol_date_stage",
        "daily_permission_snapshots",
        ["symbol", "date_uk", "daily_permission_stage"],
    )
    op.create_index(
        "ix_daily_permission_symbol_date_stage",
        "daily_permission_snapshots",
        ["symbol", "date_uk", "daily_permission_stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_daily_permission_symbol_date_stage", table_name="daily_permission_snapshots")
    op.drop_constraint("uq_daily_permission_symbol_date_stage", "daily_permission_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_daily_permission_symbol_date",
        "daily_permission_snapshots",
        ["symbol", "date_uk"],
    )

    op.drop_column("daily_permission_snapshots", "reasons_json")
    op.drop_column("daily_permission_snapshots", "confidence")
    op.drop_column("daily_permission_snapshots", "official")
    op.drop_column("daily_permission_snapshots", "permission_source")
    op.drop_column("daily_permission_snapshots", "daily_permission_stage")
    op.drop_column("daily_permission_snapshots", "computed_at_utc")
    op.drop_column("daily_permission_snapshots", "for_date")
