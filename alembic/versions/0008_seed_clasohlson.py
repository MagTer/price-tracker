"""Seed Clas Ohlson as the ninth store.

Clas Ohlson (SAP Commerce/hybris) server-renders schema.org JSON-LD, but like Rusta's it
carries only the current price: the ordinarie at a rea and the printed jämförpris exist
only in the page's BEM price markup (verified 2026-07-27 against live product pages).
The store therefore rides the store-HTML ladder tier via ``domain/extractors/
clasohlson.py``, which composes the shared JsonLdExtractor and adds what the markup
knows; plain JSON-LD stays the fallback.

No ``check_weekdays`` seeded: no weekly offer cycle worth pinning to a day — interval
mode at the 72h server default, like Rusta and the pharmacies.

Data-only revision: no DDL, so ``alembic check`` drift detection is unaffected.

Revision ID: 0008_seed_clasohlson
Revises: 0007_seed_rusta
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_seed_clasohlson"
down_revision: str | None = "0007_seed_rusta"
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
                "name": "Clas Ohlson",
                "slug": "clasohlson",
                "store_type": "retail",
                "base_url": "https://www.clasohlson.com",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "clasohlson"))
