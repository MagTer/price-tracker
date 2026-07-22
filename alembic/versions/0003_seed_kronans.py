"""Seed Kronans Apotek as the sixth store.

Kronans Apotek server-renders full schema.org Product JSON-LD (verified 2026-07-22
against a live product page: price, availability, brand, strikethrough ord.pris),
so the generic JsonLdExtractor covers it — this store needs only a registry row
(hostname match for quick-add, slug for the parser's hint lookup) and LLM hints.

Data-only revision: no DDL, so `alembic check` drift detection is unaffected.

Revision ID: 0003_seed_kronans
Revises: 0002_store_label
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_seed_kronans"
down_revision: str | None = "0002_store_label"
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
                "name": "Kronans Apotek",
                "slug": "kronans",
                "store_type": "pharmacy",
                "base_url": "https://www.kronansapotek.se",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "kronans"))
