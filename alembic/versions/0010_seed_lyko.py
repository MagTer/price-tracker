"""Seed Lyko as the tenth store.

Lyko (an Avensia Nitro storefront, like Rusta) server-renders schema.org JSON-LD, but that
node carries only the CURRENT price: the ordinarie at a campaign lives solely in the page's
``window.CURRENT_PAGE`` state (verified live 2026-07-29 — N.C.P. Hand Wash 201 published
JSON-LD price 58 while selling at 80 % off an ordinarie of 290). The store therefore rides
the store-HTML ladder tier via ``domain/extractors/lyko.py``, which reads the state's
ordinarie, structured ``size``/``unit`` package data and online availability; plain JSON-LD
stays the fallback.

``base_url`` is the apex host: Lyko serves on ``lyko.com`` with no ``www`` (quick-add matches
the pasted URL's hostname against this value, ``www.`` stripped either way).

No ``check_weekdays`` seeded: no weekly offer cycle worth pinning to a day — interval mode
at the 72h server default, like Rusta, Clas Ohlson and the pharmacies.

Data-only revision: no DDL, so ``alembic check`` drift detection is unaffected.

Revision ID: 0010_seed_lyko
Revises: 0009_check_attempts
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_seed_lyko"
down_revision: str | None = "0009_check_attempts"
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
                "name": "Lyko",
                "slug": "lyko",
                "store_type": "retail",
                "base_url": "https://lyko.com",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "lyko"))
