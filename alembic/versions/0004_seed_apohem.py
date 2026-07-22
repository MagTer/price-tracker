"""Seed Apohem as the seventh store.

Apohem server-renders full schema.org Product JSON-LD (verified 2026-07-22 against
a live product page: string price, offers as a LIST, availability, brand, ListPrice
ord.pris in priceSpecification), so the generic JsonLdExtractor covers it — like
0003 this is a registry row + LLM hints, no store-specific extractor.

Data-only revision: no DDL, so `alembic check` drift detection is unaffected.

Revision ID: 0004_seed_apohem
Revises: 0003_seed_kronans
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_seed_apohem"
down_revision: str | None = "0003_seed_kronans"
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
                "name": "Apohem",
                "slug": "apohem",
                "store_type": "pharmacy",
                "base_url": "https://www.apohem.se",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "apohem"))
