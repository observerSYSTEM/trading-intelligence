"""add broker offset fields to mt5 ingest status

Revision ID: b4c2d1e9f8a7
Revises: a1d9e4c7b2f3
Create Date: 2026-02-19 07:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b4c2d1e9f8a7"
down_revision = "a1d9e4c7b2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mt5_ingest_status", sa.Column("broker_offset_seconds", sa.Integer(), nullable=True))
    op.add_column("mt5_ingest_status", sa.Column("broker_offset_detected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("mt5_ingest_status", "broker_offset_detected_at")
    op.drop_column("mt5_ingest_status", "broker_offset_seconds")
