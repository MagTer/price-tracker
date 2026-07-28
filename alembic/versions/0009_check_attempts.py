"""Record every outgoing price check, including the ones that produced no price.

price_points is a record of what stores SAID; a failed or walled check writes nothing at
all, and since v0.29.2 a block does not even touch ``last_checked_at``. So reliability per
store has always been unanswerable from the database — the only trace was a WARNING in the
in-memory ring buffer, which resets on restart.

Additive, and deliberately shipped BEFORE the statistics page it exists to feed: a page can
be built whenever, but history cannot be backfilled. Every day without this table is a day
of evidence permanently lost.

``product_store_id`` is nullable (the quick-add preview fetches before a link exists) and
ON DELETE SET NULL, so removing a link cannot fail on an audit row while the store-level
history survives it.

Revision ID: 0009_check_attempts
Revises: 0008_seed_clasohlson
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_check_attempts"
down_revision: str | None = "0008_seed_clasohlson"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the check_attempts table."""
    op.create_table(
        "check_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("extraction_source", sa.String(length=40), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["product_store_id"], ["product_stores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_check_attempts_store_id", "check_attempts", ["store_id"])
    op.create_index("ix_check_attempts_product_store_id", "check_attempts", ["product_store_id"])
    op.create_index("ix_check_attempts_checked_at", "check_attempts", ["checked_at"])
    op.create_index("ix_check_attempts_outcome", "check_attempts", ["outcome"])


def downgrade() -> None:
    """Drop the check_attempts table."""
    op.drop_index("ix_check_attempts_outcome", table_name="check_attempts")
    op.drop_index("ix_check_attempts_checked_at", table_name="check_attempts")
    op.drop_index("ix_check_attempts_product_store_id", table_name="check_attempts")
    op.drop_index("ix_check_attempts_store_id", table_name="check_attempts")
    op.drop_table("check_attempts")
