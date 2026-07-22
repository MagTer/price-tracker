"""Check schedule becomes a STORE property; links inherit unless overridden.

The politeness schedule (when and how often we visit a site) is a property of the chain,
not of each tracked product: ICA publishes weekly offers Mondays, Willys Mondays AND
Fridays, the pharmacies have no offer cycle and get a 72h interval — all morning-aligned.
That also replaces the single ``check_weekday`` int with a ``check_weekdays`` JSONB list
(Willys needs two days).

- ``stores`` gains ``check_weekdays`` (JSONB, NULL = interval mode) and
  ``check_frequency_hours`` (NOT NULL, default 72), seeded per chain below.
- ``product_stores.check_weekday`` (int) is DROPPED, replaced by nullable
  ``check_weekdays``; ``check_frequency_hours`` becomes nullable. Both NULL = inherit
  the store schedule (domain/schedule.py owns the resolution rule).
- ALL existing links are reset to inherit. Deliberate data loss, agreed 2026-07-22:
  every per-link cadence in production is the old chain-level suggestion materialized
  at creation time (OFFER_WEEKDAYS prefill / the 72h default), not an operator decision.
  A genuinely wanted override is one edit away in the link dialog.

Revision ID: 0005_store_schedule
Revises: 0004_seed_apohem
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_store_schedule"
down_revision: str | None = "0004_seed_apohem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Chain offer cycles (0=Monday). Stores absent here keep NULL = interval mode at 72h.
_STORE_WEEKDAYS: dict[str, list[int]] = {"ica": [0], "willys": [0, 4]}


def upgrade() -> None:
    op.add_column("stores", sa.Column("check_weekdays", postgresql.JSONB(), nullable=True))
    op.add_column(
        "stores",
        sa.Column(
            "check_frequency_hours", sa.Integer(), nullable=False, server_default=sa.text("72")
        ),
    )
    for slug, weekdays in _STORE_WEEKDAYS.items():
        op.execute(
            sa.text("UPDATE stores SET check_weekdays = :days WHERE slug = :slug").bindparams(
                sa.bindparam("days", weekdays, type_=postgresql.JSONB()),
                sa.bindparam("slug", slug),
            )
        )

    op.add_column(
        "product_stores", sa.Column("check_weekdays", postgresql.JSONB(), nullable=True)
    )
    op.alter_column("product_stores", "check_frequency_hours", nullable=True)
    # Reset every link to inherit (see module docstring), which also makes the dropped
    # check_weekday column's data intentionally unmigrated.
    op.execute(sa.text("UPDATE product_stores SET check_frequency_hours = NULL"))
    op.drop_column("product_stores", "check_weekday")


def downgrade() -> None:
    op.add_column("product_stores", sa.Column("check_weekday", sa.Integer(), nullable=True))
    # Overrides collapse back to the old shape: first listed weekday, frequency or 72.
    op.execute(
        sa.text(
            "UPDATE product_stores SET check_weekday = (check_weekdays ->> 0)::int "
            "WHERE check_weekdays IS NOT NULL AND jsonb_array_length(check_weekdays) > 0"
        )
    )
    op.execute(
        sa.text(
            "UPDATE product_stores SET check_frequency_hours = 72 "
            "WHERE check_frequency_hours IS NULL"
        )
    )
    op.alter_column("product_stores", "check_frequency_hours", nullable=False)
    op.drop_column("product_stores", "check_weekdays")

    op.drop_column("stores", "check_frequency_hours")
    op.drop_column("stores", "check_weekdays")
