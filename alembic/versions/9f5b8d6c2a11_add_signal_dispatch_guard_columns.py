"""add signal dispatch guard columns

Revision ID: 9f5b8d6c2a11
Revises: b52e7e71d7f4
Create Date: 2026-02-14 13:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f5b8d6c2a11"
down_revision: Union[str, Sequence[str], None] = "b52e7e71d7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("signal_events", sa.Column("tier_min", sa.String(), nullable=True))
    op.add_column("signal_events", sa.Column("snapshot_as_of_utc", sa.DateTime(timezone=True), nullable=True))
    op.add_column("signal_events", sa.Column("dispatch_kind", sa.String(), nullable=True))
    op.create_index(
        "uq_signal_events_symbol_asof_tiermin_dispatch",
        "signal_events",
        ["symbol", "snapshot_as_of_utc", "tier_min"],
        unique=True,
        postgresql_where=sa.text("snapshot_as_of_utc IS NOT NULL AND tier_min IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_signal_events_symbol_asof_tiermin_dispatch", table_name="signal_events")
    op.drop_column("signal_events", "dispatch_kind")
    op.drop_column("signal_events", "snapshot_as_of_utc")
    op.drop_column("signal_events", "tier_min")
