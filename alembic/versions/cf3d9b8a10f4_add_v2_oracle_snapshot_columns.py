"""add v2 oracle snapshot columns

Revision ID: cf3d9b8a10f4
Revises: 9f5b8d6c2a11
Create Date: 2026-02-14 16:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cf3d9b8a10f4"
down_revision: Union[str, Sequence[str], None] = "9f5b8d6c2a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("gold_regime_daily", sa.Column("final_allowed_basic", sa.String(), nullable=True))
    op.add_column("gold_regime_daily", sa.Column("final_allowed_elite", sa.String(), nullable=True))
    op.add_column("gold_regime_daily", sa.Column("daily_bias", sa.String(), nullable=True))
    op.add_column("gold_regime_daily", sa.Column("confirm_ok", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("gold_regime_daily", "confirm_ok")
    op.drop_column("gold_regime_daily", "daily_bias")
    op.drop_column("gold_regime_daily", "final_allowed_elite")
    op.drop_column("gold_regime_daily", "final_allowed_basic")
