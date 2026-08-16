"""Seed JYSK as the eleventh store.

JYSK (Drupal + React SSR) server-renders schema.org JSON-LD that fails the tracker twice
over, both silently (verified live 2026-08-16): a multi-variant product publishes a
``ProductGroup`` whose variants hang off ``hasVariant``, which the generic Product walk
never descends into — so the page falls straight to the LLM — and the price it publishes
is what one piece costs TODAY, never the ordinarie, so a rea would be recorded as a plain
price (Badrumsmatta SANDHEM: JSON-LD 75 against a printed "Ordinarie pris: 149:- /st.").
The store therefore rides the store-HTML ladder tier via ``domain/extractors/jysk.py``,
which resolves the URL's ``?article=`` variant and reads the rea and flerköp markup;
plain JSON-LD stays the fallback.

``base_url`` is the apex host: JYSK serves on ``jysk.se`` with no ``www`` (quick-add
matches the pasted URL's hostname against this value, ``www.`` stripped either way).

No ``check_weekdays`` seeded: no weekly offer cycle worth pinning to a day — interval
mode at the 72h server default, like Rusta, Clas Ohlson, Lyko and the pharmacies.

Data-only revision: no DDL, so ``alembic check`` drift detection is unaffected.

Revision ID: 0012_seed_jysk
Revises: 0011_package_precision
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_seed_jysk"
down_revision: str | None = "0011_package_precision"
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
                "name": "JYSK",
                "slug": "jysk",
                "store_type": "retail",
                "base_url": "https://jysk.se",
                "parser_config": {},
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(_stores_table).where(_stores_table.c.slug == "jysk"))
