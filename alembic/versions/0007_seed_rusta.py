"""Seed Rusta as the eighth store.

Rusta (an Avensia Nitro storefront) server-renders both schema.org JSON-LD and a richer
``window.CURRENT_PAGE`` hydration object (verified 2026-07-27 against three live product
pages). The JSON-LD carries only the current price, so the store gets a page-state
extractor (``domain/extractors/rusta.py``) that reads the ordinarie at a rea, the printed
jämförpris and the stock level from CURRENT_PAGE; JSON-LD stays the in-page fallback.

No ``check_weekdays`` seeded: Rusta has no weekly offer cycle worth pinning to a day, so
the store runs in interval mode at the 72h server default, like the pharmacies.

Data-only revision: no DDL, so ``alembic check`` drift detection is unaffected.

Revision ID: 0007_seed_rusta
Revises: 0006_normalize_categories
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_seed_rusta"
down_revision: str | None = "0006_normalize_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_stores_table = sa.table(
    "stores",
    sa.column("name", sa.String),
    sa.column("slug", sa.String),
    sa.column("store_type", sa.String),
    sa.column("base_url", sa.String),
    sa.column("parser_config", postgresql.JSONB),
    sa.column("is_active", sa.Boolean),
)


def upgrade() -> None:
    op.bulk_insert(
        _stores_table,
        [
            {
                "name": "Rusta",
                "slug": "rusta",
                "store_type": "retail",
                "base_url": "https://www.rusta.com",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "rusta"))
