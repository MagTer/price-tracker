"""Static gates — cheap insurance against drift that no runtime test would catch.

These parse source files as text. They exist because the two failure modes below are silent:
a g/kg factor slip makes one link look 1000x cheaper than every other and win every comparison,
and an Alembic revision rewritten under a stale id applies nothing at all.
"""

import ast
import re
from decimal import Decimal
from pathlib import Path

from domain.pricing import PKG_UNITS

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
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


# --- MODEL-02: no link may be resolved by the (product_id, store_id) pair -------------------
#
# Dropping uq_product_store makes "two pack sizes at one store" legal — and makes every query
# that resolves a link on that pair latently multi-valued. `.scalar_one_or_none()` on such a
# query raises MultipleResultsFound, which surfaces as HTTP 500 on the exact scenario this
# phase exists to enable. This gate fails the build if that shape ever comes back.
#
# The detector is AST-based, not a regex: comments and strings are invisible to it by
# construction, so prose mentioning the bug cannot fail the build, and — critically — a JOIN
# condition (`ProductStore.store_id == Store.id`) is not confused with a FILTER
# (`ProductStore.store_id == store_uuid`). Both identifiers appear legitimately in joins all
# over the codebase; a gate that flagged either one alone would be permanently red and would
# be switched off within a week.

# The pre-phase lookup, verbatim (admin.py:596-600 before this phase). The detector MUST flag
# it — a gate that cannot detect the bug it exists to prevent is a false assurance, not a gate.
_BAD_SAMPLE = """
stmt = select(ProductStore).where(
    ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
)
"""

# The join shape that must NOT be flagged: both columns appear, but each is bound to its
# parent table's primary key. This is `POST /check/{product_store_id}` (the correct pattern).
_GOOD_SAMPLE = """
stmt = (
    select(ProductStore, Store, Product)
    .join(Store, ProductStore.store_id == Store.id)
    .join(Product, ProductStore.product_id == Product.id)
    .where(ProductStore.id == ps_uuid)
)
"""

_JOIN_TARGETS = {("Store", "id"), ("Product", "id"), ("ProductStore", "id")}


def _is_ps_column(node: ast.expr, column: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == column
        and isinstance(node.value, ast.Name)
        and node.value.id == "ProductStore"
    )


def _is_join_target(node: ast.expr) -> bool:
    """True for `Store.id` / `Product.id` / `ProductStore.id` — i.e. a join condition, not a filter."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and (node.value.id, node.attr) in _JOIN_TARGETS
    )


def _filters_on(node: ast.AST, column: str) -> bool:
    """Does this subtree constrain ProductStore.<column> against a scalar (not a join target)?"""
    for cmp_node in ast.walk(node):
        if not isinstance(cmp_node, ast.Compare) or not isinstance(cmp_node.ops[0], ast.Eq):
            continue
        left, right = cmp_node.left, cmp_node.comparators[0]
        for a, b in ((left, right), (right, left)):
            if _is_ps_column(a, column) and not _is_join_target(b):
                return True
    return False


def _selects_product_store(node: ast.AST) -> bool:
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "select"
            and any(isinstance(a, ast.Name) and a.id == "ProductStore" for a in call.args)
        ):
            return True
    return False


def _find_pair_keyed_lookups(source: str, label: str) -> list[str]:
    """Return a finding per statement that resolves a ProductStore by (product_id, store_id)."""
    tree = ast.parse(source)
    findings: list[str] = []

    # Only leaf statements — a compound statement (`with`, `try`) would swallow its whole body
    # and merge unrelated queries into one subtree.
    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.Assign | ast.AnnAssign | ast.Expr | ast.Return):
            continue
        if not _selects_product_store(stmt):
            continue
        if _filters_on(stmt, "product_id") and _filters_on(stmt, "store_id"):
            findings.append(f"{label}:{stmt.lineno}")

    return findings


def test_pair_keyed_lookup_detector_flags_the_pre_phase_shape() -> None:
    """Self-check: the gate below is only worth anything if it can actually fail."""
    assert _find_pair_keyed_lookups(_BAD_SAMPLE, "<bad-sample>"), (
        "The detector did not flag the pre-phase (product_id, store_id) lookup. "
        "The gate is a false assurance — fix the detector before trusting it."
    )
    assert not _find_pair_keyed_lookups(_GOOD_SAMPLE, "<good-sample>"), (
        "The detector flagged a JOIN condition as a pair-keyed filter. It would be permanently "
        "red against the real codebase and would get disabled."
    )


def test_no_link_lookup_by_product_store_pair() -> None:
    """MODEL-02: no query in src/ may resolve a ProductStore by the (product_id, store_id) pair.

    That pair stopped being unique when D-01 dropped uq_product_store. Links are addressed by
    their own id (`/product-stores/{product_store_id}`) or, in the import path, by store_url.
    """
    findings: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        findings.extend(_find_pair_keyed_lookups(path.read_text(encoding="utf-8"), rel))

    assert not findings, (
        "A ProductStore is resolved by the (product_id, store_id) pair at: "
        + ", ".join(findings)
        + ". That pair is no longer unique — the query raises MultipleResultsFound (HTTP 500) "
        "as soon as a product has two pack sizes at one store. Key on ProductStore.id."
    )
