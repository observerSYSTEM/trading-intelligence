"""add stripe webhook idempotency table

Revision ID: 1d4a9c7e2b31
Revises: 8b7c1a9d4e22
Create Date: 2026-02-16 00:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1d4a9c7e2b31"
down_revision: Union[str, Sequence[str], None] = "8b7c1a9d4e22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_idempotency_event_id"),
    )
    op.create_index("ix_stripe_webhook_idempotency_seen_at", "stripe_webhook_idempotency", ["seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stripe_webhook_idempotency_seen_at", table_name="stripe_webhook_idempotency")
    op.drop_table("stripe_webhook_idempotency")

