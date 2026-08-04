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
ADMIN_MODULE = SRC_ROOT / "api" / "admin.py"
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
    """True for `Store.id` / `Product.id` / `ProductStore.id`.

    I.e. a join condition, not a filter.
    """
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


# --- AUTHZ: every route must sit behind the ONE write gate ---------------------------------
#
# The admin/reader split (v0.29.0) is enforced by a single router-level dependency that keys
# on the HTTP method: reads for everyone the Entra gate let in, writes for ALLOWED_ENTRA_EMAIL
# only. That design is only safe while it is the ONLY way a route gets registered — a second
# APIRouter, or a route hung straight off the FastAPI app in this module, would be an
# unauthenticated, unauthorized endpoint that no runtime test would think to call.
#
# Both failure modes are silent: the endpoint works perfectly, for everybody.

_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

# A route registered on something other than the gated router. The detector MUST flag it.
_UNGATED_ROUTE_SAMPLE = """
open_router = APIRouter()

@open_router.post("/danger")
async def danger() -> None:
    ...
"""


def _router_gate_dependencies(source: str) -> list[str]:
    """Names passed to Depends(...) in the `router = APIRouter(dependencies=[...])` call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if call.func.id != "APIRouter":
            continue
        for kw in call.keywords:
            if kw.arg != "dependencies" or not isinstance(kw.value, ast.List):
                continue
            return [
                el.args[0].id
                for el in kw.value.elts
                if isinstance(el, ast.Call)
                and isinstance(el.func, ast.Name)
                and el.func.id == "Depends"
                and el.args
                and isinstance(el.args[0], ast.Name)
            ]
    return []


def _route_decorator_owners(source: str) -> set[str]:
    """Every object a route decorator is applied to — `@router.get` yields "router"."""
    owners: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            func = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _ROUTE_METHODS
                and isinstance(func.value, ast.Name)
            ):
                owners.add(func.value.id)
    return owners


def test_ungated_route_detector_flags_a_second_router() -> None:
    """Self-check: the gate below is only worth anything if it can actually fail."""
    assert _route_decorator_owners(_UNGATED_ROUTE_SAMPLE) == {"open_router"}


def test_admin_router_carries_the_write_gate() -> None:
    """AUTHZ-01: the router's dependency list IS the authorization model.

    require_admin_for_writes authenticates every caller and refuses a state-changing
    method from anyone who is not ALLOWED_ENTRA_EMAIL. Drop it and all 14 write endpoints
    silently become open to every reader in the Entra tenant.
    """
    dependencies = _router_gate_dependencies(ADMIN_MODULE.read_text(encoding="utf-8"))

    assert "require_admin_for_writes" in dependencies, (
        "src/api/admin.py's APIRouter no longer declares Depends(require_admin_for_writes). "
        f"Found dependencies: {dependencies or 'none'}. Every write endpoint is now open to "
        "any authenticated reader."
    )


def test_every_admin_route_is_registered_on_the_gated_router() -> None:
    """AUTHZ-02: no route in admin.py may be hung off anything but the gated `router`.

    The gate is router-level, so a route registered on a second APIRouter — or straight on
    the app — inherits nothing and is reachable by anyone the ingress lets through.
    """
    owners = _route_decorator_owners(ADMIN_MODULE.read_text(encoding="utf-8"))

    assert owners == {"router"}, (
        "Routes in src/api/admin.py are registered on "
        + ", ".join(sorted(owners))
        + " — only the gated `router` may carry routes, or the endpoint bypasses "
        "require_admin_for_writes entirely."
    )


def test_category_selects_use_the_injected_placeholder() -> None:
    """The three category dialogs must render from the ONE canonical list, not a free-text box.

    render_admin injects domain.categories.PRODUCT_CATEGORIES into a <!--CATEGORY_OPTIONS-->
    placeholder, so there is no second copy of the list to drift. If someone reverts a <select>
    back to a free-text <input name="category">, or drops the placeholder, the field silently
    goes back to accepting arbitrary strings — this catches that.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert html.count("<!--CATEGORY_OPTIONS-->") == 3, (
        "Expected exactly 3 <!--CATEGORY_OPTIONS--> placeholders (create / edit / quick-add). "
        "A category <select> lost its placeholder, so render_admin injects nothing into it."
    )
    assert 'name="category"' not in html or 'type="text" name="category"' not in html, (
        'A free-text <input name="category"> is back — category must be a <select> fed by the '
        "canonical taxonomy, not an open string field."
    )


def _perform_price_check_calls() -> list[tuple[Path, ast.Call]]:
    """Every `perform_price_check(...)` CALL in src/ (the definition itself is not a call)."""
    found: list[tuple[Path, ast.Call]] = []
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "perform_price_check"
            ):
                found.append((path, node))
    return found


def test_every_price_check_caller_records_its_attempt() -> None:
    """A caller that omits `attempt_log` silently stops recording — and looks like idleness.

    check_attempts is the ONLY durable evidence of a check that produced no price: a failure
    writes no price point, and a blocked check does not even touch `last_checked_at`. So a
    caller that forgets the argument does not degrade the data, it removes it — and the gap
    reads exactly like "nothing was due", which is the misreading the table exists to prevent.

    `attempt_log` defaults to None on purpose (several hundred existing tests call the flow
    with mock sessions and no database), so the default cannot be the enforcement. This is.
    """
    calls = _perform_price_check_calls()
    assert calls, "No perform_price_check call sites found — has the flow been renamed?"

    missing = [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
        for path, node in calls
        if not any(kw.arg == "attempt_log" for kw in node.keywords)
    ]
    assert not missing, (
        "perform_price_check called without attempt_log at: "
        + ", ".join(missing)
        + " — that check would leave no trace when it fails or is blocked."
    )


_FILTER_CONTROLS_RE = re.compile(
    r"const PRODUCT_FILTER_CONTROLS\s*=\s*\[(.*?)\];",
    re.DOTALL,
)


def test_product_filter_controls_exist_in_the_markup() -> None:
    """Every id the filter wiring binds to must exist in the served page.

    The wiring loop calls document.getElementById(id).addEventListener at TOP LEVEL. A renamed
    or deleted control does not degrade the filter — getElementById returns null, the property
    access throws while the script is still loading, and EVERY handler below it never binds.
    The page then renders and simply does nothing: no row actions, no dialogs, no toasts. That
    is a blank-looking bug with no error anywhere the operator will see it.

    Same shape as the aisle-order read: categoryOrder() reads #edit-product-category's options
    back out of the DOM, so that id is load-bearing too.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    table = _FILTER_CONTROLS_RE.search(html)
    assert table is not None, "PRODUCT_FILTER_CONTROLS not found in admin.html"

    ids = re.findall(r"\['([\w-]+)',\s*'(\w+)'\]", table.group(1))
    # Four <input>/<select> facets since the 3a rebuild: search, kategori, butik, täckning.
    # Status and sort order moved into the chip row (#product-chips), which is delegated and
    # therefore cannot throw at load — they live in the same filter state without being
    # bound by id here.
    assert len(ids) == 4, f"Expected 4 filter controls, parsed {len(ids)} — has the shape changed?"

    for element_id, _key in ids + [("edit-product-category", "category")]:
        assert f'id="{element_id}"' in html, (
            f"admin.html binds to #{element_id} but never renders it — the script throws while "
            "loading and the whole page goes inert."
        )


def test_every_getelementbyid_literal_resolves_to_markup() -> None:
    """EVERY id the script looks up must exist in the served page, not just the six
    filter controls.

    The failure mode is the one the filter-controls gate describes — a null lookup at
    script top level kills every handler below it and the page renders inert — but two
    bindings added after that gate (#filter-more's phone fold, #stats-container's sort
    delegation) had the same shape and no coverage. This gate is the general form: all
    getElementById string literals in the JS fragment, checked against the template AND
    the wrapper markup admin.py renders around it (which owns e.g. #user-email).

    Every id in this app is static by design (the portal is ONE served page); if a
    legitimately dynamic id ever appears, exempt it here explicitly with a comment.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    admin_py = (SRC_ROOT / "api" / "admin.py").read_text(encoding="utf-8")
    js = html.split("<!-- SECTION_SEPARATOR -->")[2]

    ids = sorted(set(re.findall(r"getElementById\('([\w-]+)'\)", js)))
    assert len(ids) >= 60, f"Parsed only {len(ids)} ids — has the extraction broken?"

    missing = [i for i in ids if f'id="{i}"' not in html and f'id="{i}"' not in admin_py]
    assert not missing, (
        f"admin.html's script binds to {missing} but no markup renders them — "
        "getElementById returns null, the script throws while loading, and the page goes inert."
    )
