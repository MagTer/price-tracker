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
