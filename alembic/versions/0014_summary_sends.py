"""The buy-list email remembers, durably, that it already went out.

The dedup that stopped a second send was ``PriceCheckScheduler._last_summary_date`` — in
memory, so every restart forgot it. On a check day after 12:00 Swedish the loop then found
the mail "due, not yet sent" and sent it again. That was a deliberate trade-off when it was
written ("a rare duplicate after an afternoon restart is the accepted cost"), and its
premise expired: v0.59.0 doubled the mail's days to Monday AND Friday, and a deploy IS a
restart, so the duplicate lands exactly when a release does. Observed on prod 2026-09-04 —
the v0.61.0 deploy mailed the buy list 6 seconds after startup, hours after that morning's
send.

The row is also the answer to a question nothing else could answer: did the mail go out,
and what did it carry? Only a send that returned True writes one, so "no row" means "not
sent" rather than "not recorded" — the check_attempts rule, applied to the one outgoing
message this app produces.

``sent_on`` is the LOCAL (Europe/Stockholm) check day and the primary key, so a duplicate
is impossible by construction rather than by remembering.

Revision ID: 0014_summary_sends
Revises: 0013_store_weekdays_mon_fri
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_summary_sends"
down_revision: str | None = "0013_store_weekdays_mon_fri"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "summary_sends",
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("deals_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "had_quality_issues", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.PrimaryKeyConstraint("sent_on"),
    )


def downgrade() -> None:
    op.drop_table("summary_sends")
