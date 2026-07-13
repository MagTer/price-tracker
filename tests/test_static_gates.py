"""Static gates — cheap insurance against drift that no runtime test would catch.

These parse source files as text. They exist because the two failure modes below are silent:
a g/kg factor slip makes one link look 1000x cheaper than every other and win every comparison,
and an Alembic revision rewritten under a stale id applies nothing at all.
"""

import re
from decimal import Decimal
from pathlib import Path

from domain.pricing import PKG_UNITS

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_HTML = REPO_ROOT / "src" / "api" / "templates" / "admin.html"
INITIAL_MIGRATION = REPO_ROOT / "alembic" / "versions" / "0001_initial.py"

_JS_TABLE_RE = re.compile(r"const PKG_UNITS\s*=\s*\{(.*?)\};", re.DOTALL)
_JS_ENTRY_RE = re.compile(
    r"(\w+):\s*\{\s*canonical:\s*'([^']+)'\s*,\s*factor:\s*([0-9.]+)\s*,",
)


def _parse_js_pkg_units() -> dict[str, tuple[str, Decimal]]:
    """Pull the PKG_UNITS object literal out of admin.html."""
    table = _JS_TABLE_RE.search(ADMIN_HTML.read_text(encoding="utf-8"))
    assert table is not None, "PKG_UNITS table not found in admin.html"

    entries = _JS_ENTRY_RE.findall(table.group(1))
    assert entries, "PKG_UNITS table found but no entries parsed — has the JS shape changed?"

    return {unit: (canonical, Decimal(factor)) for unit, canonical, factor in entries}


def test_python_and_js_pkg_units_agree() -> None:
    """The Python factor table and the JS one must not drift apart (Pitfall 5)."""
    js_units = _parse_js_pkg_units()

    assert js_units.keys() == PKG_UNITS.keys(), (
        "PKG_UNITS key sets differ between admin.html and domain/pricing.py — "
        "a unit was added on one side only"
    )

    for unit, (js_canonical, js_factor) in js_units.items():
        py_canonical, py_factor = PKG_UNITS[unit]
        assert js_canonical == py_canonical, f"canonical unit for {unit!r} differs"
        assert js_factor == py_factor, f"factor for {unit!r} differs (a 1000x bug hides here)"


def test_initial_migration_declares_reshaped_columns() -> None:
    """0001_initial is the DDL image of the reshaped models, rewritten IN PLACE (D-14).

    A source-text gate, not a DB test: the real DDL-versus-ORM proof is `alembic check` at the
    phase gate. What this catches is the rewrite being stacked as a 0002 by mistake, or a column
    landing on the wrong table.
    """
    source = INITIAL_MIGRATION.read_text(encoding="utf-8")

    # Rewritten in place: the revision id is unchanged, so nothing was stacked on top of it.
    assert 'revision: str = "0001_initial"' in source
    assert "down_revision: str | Sequence[str] | None = None" in source

    # The package columns live on the link now, together with the page's own reading.
    assert '"scraped_package_quantity"' in source
    # Unit price is computed on read; only the store's printed value is stored.
    assert '"store_unit_price_sek"' in source
    assert '"unit_price_sek"' not in source
    # The URL is the link's natural key; the old (product_id, store_id) pair is gone.
    assert 'name="uq_product_stores_store_url"' in source
    assert 'name="uq_product_store"' not in source

    # D-15: the in-place rewrite is silently a no-op on an already-stamped DB. The warning that
    # says so must stay in the docstring — it is the only thing standing between the operator
    # and an app running against the old schema.
    assert "D-15" in source
    assert "docker compose down -v" in source
