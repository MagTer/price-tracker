"""Normalise free-text product categories to the fixed taxonomy.

`category` became a closed, ordered set (domain.categories) — the old free-text values the LLM
guessed ("toalettpapper", "mjölk", "Drycker", ...) must be folded into it or they linger as
values the new <select> cannot display. This is a data-only revision (no DDL — the column stays
String(100)), so `alembic check` drift detection is unaffected.

The keyword map below is a FROZEN copy of domain.categories._CATEGORY_KEYWORDS as of this
revision. A migration must not import live code — if the taxonomy is reordered or extended
later, this migration must still reproduce the exact state it produced on the day it ran.
Anything unmatched becomes NULL: an empty field the user re-picks beats a stale value.

Revision ID: 0006_normalize_categories
Revises: 0005_store_schedule
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_normalize_categories"
down_revision: str | None = "0005_store_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANONICAL = {
    "Bröd & Bageri",
    "Chark & Deli",
    "Kött & Fisk",
    "Mejeri & Ost",
    "Frukt & Grönt",
    "Fryst",
    "Skafferi",
    "Dryck",
    "Godis & Snacks",
    "Hushåll, Hygien & Apotek",
}

# Frozen copy — first substring hit wins, so order matters ("bröd" before "korv").
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("bröd", "Bröd & Bageri"),
    ("bageri", "Bröd & Bageri"),
    ("bulle", "Bröd & Bageri"),
    ("fralla", "Bröd & Bageri"),
    ("baguette", "Bröd & Bageri"),
    ("chark", "Chark & Deli"),
    ("deli", "Chark & Deli"),
    ("pålägg", "Chark & Deli"),
    ("korv", "Chark & Deli"),
    ("skinka", "Chark & Deli"),
    ("bacon", "Chark & Deli"),
    ("färdigmat", "Chark & Deli"),
    ("färdigrätt", "Chark & Deli"),
    ("kött", "Kött & Fisk"),
    ("färs", "Kött & Fisk"),
    ("fågel", "Kött & Fisk"),
    ("kyckling", "Kött & Fisk"),
    ("fläsk", "Kött & Fisk"),
    ("biff", "Kött & Fisk"),
    ("fisk", "Kött & Fisk"),
    ("lax", "Kött & Fisk"),
    ("skaldjur", "Kött & Fisk"),
    ("räk", "Kött & Fisk"),
    ("mejeri", "Mejeri & Ost"),
    ("mjölk", "Mejeri & Ost"),
    ("mjölkprodukt", "Mejeri & Ost"),
    ("yog", "Mejeri & Ost"),
    ("grädde", "Mejeri & Ost"),
    ("smör", "Mejeri & Ost"),
    ("kvarg", "Mejeri & Ost"),
    ("ägg", "Mejeri & Ost"),
    ("frukt", "Frukt & Grönt"),
    ("grönt", "Frukt & Grönt"),
    ("grönsak", "Frukt & Grönt"),
    ("sallad", "Frukt & Grönt"),
    ("banan", "Frukt & Grönt"),
    ("äpple", "Frukt & Grönt"),
    ("tomat", "Frukt & Grönt"),
    ("potatis", "Frukt & Grönt"),
    ("fryst", "Fryst"),
    ("frys", "Fryst"),
    ("glass", "Fryst"),
    ("skafferi", "Skafferi"),
    ("pasta", "Skafferi"),
    ("konserv", "Skafferi"),
    ("sås", "Skafferi"),
    ("mjöl", "Skafferi"),
    ("krydd", "Skafferi"),
    ("flingor", "Skafferi"),
    ("müsli", "Skafferi"),
    ("torrvaror", "Skafferi"),
    ("olja", "Skafferi"),
    ("socker", "Skafferi"),
    ("dryck", "Dryck"),
    ("läsk", "Dryck"),
    ("vatten", "Dryck"),
    ("juice", "Dryck"),
    ("saft", "Dryck"),
    ("kaffe", "Dryck"),
    ("godis", "Godis & Snacks"),
    ("snack", "Godis & Snacks"),
    ("chips", "Godis & Snacks"),
    ("choklad", "Godis & Snacks"),
    ("nötter", "Godis & Snacks"),
    ("kex", "Godis & Snacks"),
    ("hushåll", "Hushåll, Hygien & Apotek"),
    ("hygien", "Hushåll, Hygien & Apotek"),
    ("städ", "Hushåll, Hygien & Apotek"),
    ("tvätt", "Hushåll, Hygien & Apotek"),
    ("papper", "Hushåll, Hygien & Apotek"),
    ("toalett", "Hushåll, Hygien & Apotek"),
    ("schampo", "Hushåll, Hygien & Apotek"),
    ("tvål", "Hushåll, Hygien & Apotek"),
    ("tand", "Hushåll, Hygien & Apotek"),
    ("blöj", "Hushåll, Hygien & Apotek"),
    ("apotek", "Hushåll, Hygien & Apotek"),
    ("läkemedel", "Hushåll, Hygien & Apotek"),
    ("receptfritt", "Hushåll, Hygien & Apotek"),
    ("kosttillskott", "Hushåll, Hygien & Apotek"),
    ("tillskott", "Hushåll, Hygien & Apotek"),
    ("vitamin", "Hushåll, Hygien & Apotek"),
    ("medicin", "Hushåll, Hygien & Apotek"),
    ("plåster", "Hushåll, Hygien & Apotek"),
    # Short substrings last (see domain.categories): "ost" hides in "kosttillskott", "fil" in
    # "filmjölk"/"oxfilé". Only a bare "ost"/"fil" falls through to Mejeri.
    ("ost", "Mejeri & Ost"),
    ("fil", "Mejeri & Ost"),
)

_products = sa.table(
    "products",
    sa.column("id", sa.String),
    sa.column("category", sa.String),
)


def _normalize(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if not v or v in _CANONICAL:
        return v or None
    lowered = v.lower()
    for keyword, category in _KEYWORDS:
        if keyword in lowered:
            return category
    return None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(_products.c.id, _products.c.category)).fetchall()
    for row_id, category in rows:
        new_value = _normalize(category)
        if new_value != category:
            bind.execute(
                sa.update(_products)
                .where(_products.c.id == row_id)
                .values(category=new_value)
            )


def downgrade() -> None:
    # Irreversible: the original free-text values are not recoverable. No-op so the chain can
    # still be stepped down without erroring — the canonical values remain, which is harmless.
    pass
