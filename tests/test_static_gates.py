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
    # Tag check, not a literal-string check: the old assertion was
    # `'name="category"' not in html or '<literal>' not in html`, whose left disjunct is
    # permanently False (the <select> carries name="category") — so it reduced to one
    # exact attribute ORDER never appearing, and an <input name="category" type="text">
    # passed it clean. A gate that cannot fail is worse than no gate.
    category_tags = re.findall(r"<(\w+)[^>]*\bname=\"category\"", html)
    assert category_tags and set(category_tags) == {"select"}, (
        f"A category field is not a <select> (found: {category_tags}) — category must be "
        "a <select> fed by the canonical taxonomy, not an open string field."
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
    the shell template rendered around it (templates/shell.html — sidebar, header and
    mobile chrome, which own e.g. #user-email; string literals in admin.py until v0.51.0).

    Every id in this app is static by design (the portal is ONE served page); if a
    legitimately dynamic id ever appears, exempt it here explicitly with a comment.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    shell = (ADMIN_HTML.parent / "shell.html").read_text(encoding="utf-8")
    js = html.split("<!-- SECTION_SEPARATOR -->")[2]

    ids = sorted(set(re.findall(r"getElementById\('([\w-]+)'\)", js)))
    assert len(ids) >= 60, f"Parsed only {len(ids)} ids — has the extraction broken?"

    # The regex above is blind to every NON-literal lookup — same failure mode, no
    # coverage. These are the constructed ids the JS actually resolves today; a new
    # dynamic lookup gets added HERE, or it ships ungated.
    dynamic_ids = [
        # getElementById(prefix + '-pkg-…') — the packaging chain, three dialog prefixes.
        *[
            f"{prefix}-pkg-{part}"
            for prefix in ("qa", "link", "edit")
            for part in ("amount", "label", "amount-label", "entry-units")
        ],
        # The scheduler footer: desktop ('') and mobile ('-m') suffixes.
        *[f"sched-state{sfx}" for sfx in ("", "-m")],
        *[f"sched-state-text{sfx}" for sfx in ("", "-m")],
        *[f"sched-when{sfx}" for sfx in ("", "-m")],
        # num('…') in the watch dialog — indirected through a local helper.
        "edit-watch-unit-target",
        "edit-watch-target",
    ]
    ids = sorted(set(ids) | set(dynamic_ids))

    missing = [i for i in ids if f'id="{i}"' not in html and f'id="{i}"' not in shell]
    assert not missing, (
        f"admin.html's script binds to {missing} but no markup renders them — "
        "getElementById returns null, the script throws while loading, and the page goes inert."
    )


_PKG_AMOUNT_INPUT_RE = re.compile(r'<input[^>]*id="[\w-]*pkg-amount"[^>]*>')
_STEP_ASSIGN_RE = re.compile(r"\.step\s*=\s*([^;]+);")


def test_the_package_amount_field_never_narrows_below_the_stored_precision() -> None:
    """A package amount is stored to four decimals; the input must not refuse three.

    This has now bitten twice, from two different places, and it is invisible to every
    other test because the failure is the BROWSER's constraint validation: with
    step="0.01" the field holding 0,024 kg (a 24 g sachet — the v0.45.0 case, which is
    exactly why the column is Numeric(12, 4) and _QUANTUM is 0.0001) reports
    stepMismatch, the form refuses to submit, and nothing reaches the server to be
    tested. Entering the SAME amount as 24 g goes through, because 24 is a multiple of
    0.01 — so it reads as an arbitrary GUI lock rather than a validation rule.

    v0.45.0 fixed the three markup attributes and left the JS that overwrites them
    (pkgFieldsChanged runs on every dialog open via pkgSetEntryUnit), so the attribute
    was correct in the file and wrong in the browser. Both places are gated here.

    'st' is the one legitimate narrowing: a count is whole, so step='1' stays.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")

    inputs = _PKG_AMOUNT_INPUT_RE.findall(html)
    assert len(inputs) == 3, (
        f"Expected 3 package-amount inputs (quick-add, link, edit), found {len(inputs)}"
    )
    for tag in inputs:
        assert 'step="any"' in tag, (
            f"package-amount input declares a fixed step: {tag}\n"
            "A 24 g sachet is 0,024 kg and the browser will refuse to submit it."
        )

    js = html.split("<!-- SECTION_SEPARATOR -->")[2]
    assignments = _STEP_ASSIGN_RE.findall(js)
    assert assignments, "no .step assignment found — has pkgFieldsChanged changed shape?"
    for expr in assignments:
        # Whole numbers for a count, 'any' for everything else. A decimal literal here is
        # the bug: it silently replaces the markup's step="any" before the operator types.
        assert "'any'" in expr, (
            f'.step is assigned {expr.strip()!r} — this overwrites step="any" on dialog '
            "open and locks the field to that many decimals."
        )
        assert not re.search(r"'0\.\d+'", expr), (
            f".step is assigned a decimal literal in {expr.strip()!r} — the 0,024 kg lock."
        )


_HISTORY_CLOSE_BUTTON_RE = re.compile(
    r'<div class="modal" id="modal-price-history".*?<div class="modal-header">(.*?)</div>',
    re.DOTALL,
)


def test_the_drill_in_is_opened_and_closed_only_through_the_hash() -> None:
    """The URL is the drill-in's state; a second way in or out makes them disagree.

    The price-history modal is addressable (`#/<page>?produkt=<id>`, v0.56.0) and opening it
    pushes a history entry, which is what makes the browser's back button close it. Both
    properties rest on ONE rule: the hash is the only state. Two ways this breaks silently —

    (1) an entry point calling showPriceHistory() directly opens the modal with the address
        bar still naming the page behind it, so back exits the app and the link cannot be
        pasted to anyone;
    (2) the × falling back to closeModal() hides the dialog while the entry it pushed stays
        on the stack, so the URL names a product nobody is looking at and one back press
        re-opens it.

    Neither shows up in a runtime test of the endpoints — nothing reaches the API — and
    neither looks wrong on screen at the moment it happens. Same both-places shape as the
    package-step gate above: the markup half and the JS half are checked here together.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")

    header = _HISTORY_CLOSE_BUTTON_RE.search(html)
    assert header is not None, "price-history modal header not found — has the markup moved?"
    assert "closePriceHistory()" in header.group(1), (
        "the price-history modal's close button no longer calls closePriceHistory() — "
        "hiding it with closeModal() leaves the pushed history entry and the ?produkt= hash "
        "behind, so the URL names a product that is not on screen."
    )

    js = html.split("<!-- SECTION_SEPARATOR -->")[2]
    call_sites = re.findall(r"showPriceHistory\(", js)
    # Its own definition, plus the ONE call in syncHistoryToHash. An entry point must set
    # the hash (openPriceHistory) and let the hashchange do the opening.
    assert len(call_sites) == 2, (
        f"showPriceHistory( appears {len(call_sites)} times, expected 2 (the definition and "
        "the single call in syncHistoryToHash). An entry point that calls it directly opens "
        "the drill-in without a URL and without a history entry — back then leaves the app."
    )

    # Each way IN is a rendered attribute plus a delegated handler that reads it, written far
    # apart in the file. Half of that pair is a button that looks exactly like the working one
    # and does nothing when clicked — nothing on screen says so, and no runtime test reaches
    # it: the drill-in is pure client-side, so the endpoints stay green either way.
    entry_points = (("data-deal-action", "Att köpa"), ("data-stats-product", "Prisutveckling"))
    for attribute, page in entry_points:
        assert f'{attribute}="' in js, (
            f"{page}'s product name no longer renders {attribute} — the drill-in has lost "
            "an entry point."
        )
        assert f"closest('button[{attribute}" in js, (
            f"nothing handles {attribute} clicks any more, so {page}'s product name is a "
            "button that does nothing. It must reach openPriceHistory (never showPriceHistory)."
        )


_MODAL_OPEN_RE = re.compile(r'<div class="modal" id="([^"]+)"([^>]*)>')
_LABEL_RE = re.compile(
    r"<label([^>]*)>((?:(?!</label>).)*?)</label>(\s*)(<[a-z]+\b[^>]*>)?", re.DOTALL
)


def _served_markup(html: str) -> str:
    """The markup halves of the template — everything BEFORE the script fragment.

    admin.html is three fragments (markup, CSS, script) and the markup is fragment 0, not 1.
    Getting that index wrong is not a loud failure: the gate then scans a chunk with no
    dialogs and no labels in it, finds nothing to complain about, and passes for the wrong
    reason. Written as a slice so the fragment layout, not a magic index, is what it tracks.
    """
    return "".join(html.split("<!-- SECTION_SEPARATOR -->")[:-1])


def _markup_ids(html: str) -> set[str]:
    """Every id in the SERVED markup — the script half's template literals are not DOM ids."""
    return set(re.findall(r'\bid="([^"]+)"', _served_markup(html)))


def test_every_dialog_carries_its_semantics() -> None:
    """A dialog without role/aria-modal/aria-labelledby is an anonymous div to a reader.

    The app has a READER role — the household members the Entra gate lets in — and v0.29.0
    exists because one of them actually logged in. This is the population that most often
    needs the semantics, and none of it is visible on screen, so nothing about the page
    looks wrong when it rots: the dialog simply stops announcing itself.

    aria-labelledby is checked against the markup's real ids because a typo'd reference is
    WORSE than none — the reader announces an unnamed dialog either way, but the attribute
    claims the name is handled.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    ids = _markup_ids(html)
    modals = _MODAL_OPEN_RE.findall(html)
    assert modals, "no .modal elements found — has the markup moved?"

    for modal_id, attrs in modals:
        assert 'role="dialog"' in attrs, f'{modal_id} is missing role="dialog"'
        assert 'aria-modal="true"' in attrs, f'{modal_id} is missing aria-modal="true"'
        labelled = re.search(r'aria-labelledby="([^"]+)"', attrs)
        assert labelled, f"{modal_id} is missing aria-labelledby"
        assert labelled.group(1) in ids, (
            f"{modal_id} points aria-labelledby at {labelled.group(1)!r}, which no element "
            "in the markup carries — the dialog announces itself as unnamed."
        )


def test_every_form_label_is_attached_to_its_control() -> None:
    """A label that is merely NEXT to its input names nothing.

    Two valid shapes, and both are accepted here: an explicit `for=` resolving to a real id,
    or the control nested INSIDE the label (implicit association, which is how the checkbox
    rows are written). What fails is the third shape — a bare <label> followed by a control —
    because it looks identical on screen and leaves the field unnamed in a screen reader.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    markup = _served_markup(html)
    ids = _markup_ids(html)

    unattached: list[str] = []
    for attrs, text, _gap, following in _LABEL_RE.findall(markup):
        if re.search(r"<(input|select|textarea)\b", text):
            continue  # wrapping label — associated by nesting
        for_attr = re.search(r'\bfor="([^"]+)"', attrs)
        if for_attr:
            assert for_attr.group(1) in ids, (
                f"label for={for_attr.group(1)!r} points at no element in the markup"
            )
            continue
        # A caption for a composite widget is fine as long as the widget claims it.
        if following and "aria-labelledby" in following:
            continue
        if following and re.match(r"<(input|select|textarea)\b", following):
            unattached.append(re.sub(r"\s+", " ", text).strip()[:40])

    assert not unattached, (
        "these labels sit beside a control without naming it (add for=, or nest the "
        f"control inside the label): {unattached}"
    )


def test_a_dialog_is_only_ever_opened_through_open_modal() -> None:
    """aria-modal="true" hides everything outside the dialog from assistive tech.

    That makes focus placement part of the contract, not a nicety: a dialog shown by setting
    `display` directly leaves focus on the button behind it, which the reader can no longer
    describe or reach. openModal() is the one place that shows a dialog AND moves focus, and
    closeModal()/syncHistoryToHash hand it back. Ten call sites used to do it by hand.
    """
    js = ADMIN_HTML.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")[2]
    direct = re.findall(r"getElementById\('(modal-[a-z-]+)'\)\.style\.display\s*=\s*'flex'", js)
    assert not direct, (
        f"these dialogs are shown without openModal(), so focus stays outside them: {direct}"
    )


def test_the_toast_timer_is_cleared_before_it_is_reset() -> None:
    """Without clearTimeout, a second toast inherits the FIRST one's deadline.

    Measured shape of the bug: toast A at t=0 sets a 3 s timer; toast B at t=2 s replaces the
    text but not the timer, so B is wiped after one second. The longer the message the more
    likely it is the one cut short — which is exactly backwards, and it hit the import
    summary ("Import klar: N skapade, M bevakningar"), one of the few toasts carrying counts
    a reader cannot recover anywhere else.
    """
    js = ADMIN_HTML.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")[2]
    body = re.search(r"function showToast\([^)]*\)\s*\{(.*?)\n\}", js, re.DOTALL)
    assert body is not None, "showToast not found — has it been renamed?"
    assert "clearTimeout" in body.group(1), (
        "showToast no longer clears the pending timer: the next toast will be cut short by "
        "the previous one's deadline."
    )


def test_quick_add_rekeys_the_package_amount_instead_of_carrying_it_across_units() -> None:
    """An amount entered in one canonical unit must not survive a change to another.

    pkgInitChain takes the amount as an ARGUMENT, so a call site that reads the field back
    and passes it through carries 0,27 — parsed as kg out of a "…270g" title — into a
    product counted in `st`, where it means 0,27 STYCK. Nothing server-side sees it: the
    quick-add form never submits, because pkgFieldsChanged sets step="1" for a count and the
    browser refuses with "the two nearest valid values are 0 and 1". That is v0.53.3's shape
    — a correct rule delivered as an unreadable message — and it sits on the normal path for
    a one-size product, where the title says 270 g and the product compares per styck.

    Both call sites are the pair: qaUnitChanged is the operator switching Enhet on a NEW
    product, qaProductChoiceChanged is them attaching the link to an EXISTING product whose
    unit differs. Fixing one leaves the other doing it, and no runtime test can see either —
    the form errors in the browser, not in the API.
    """
    js = ADMIN_HTML.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")[2]
    assert "function pkgRekeyUnit(" in js, "pkgRekeyUnit is gone — has the re-key moved?"

    for name in ("qaUnitChanged", "qaProductChoiceChanged"):
        body = re.search(rf"function {name}\([^)]*\)\s*\{{(.*?)\n\}}", js, re.DOTALL)
        assert body is not None, f"{name} not found — has it been renamed?"
        assert "pkgRekeyUnit(" in body.group(1), (
            f"{name} no longer re-keys through pkgRekeyUnit, so the amount survives a change "
            "of canonical unit and the form silently refuses to submit."
        )
        assert "pkgInitChain(" not in body.group(1), (
            f"{name} calls pkgInitChain directly again — that is the shape that carries the "
            "old unit's amount across. Go through pkgRekeyUnit."
        )


def test_the_stores_printed_jfr_pris_is_never_labelled_with_the_products_unit() -> None:
    """ "Butiken anger" is in the STORE's measure, and unitKr() labels with the PRODUCT's.

    ICA prints toalettpapper in kr/kg while the product is counted in styck, so rendering
    that cell with unitKr() would print "63,20 kr/st" — a wrong measure stated with
    confidence, which is worse than the bare number it replaced. storeSaysKr() takes the
    measure from the wire (`store_unit_price_unit`, read by pricing.printed_measure) and
    falls back to bare kronor when no extractor recorded one.
    """
    js = ADMIN_HTML.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")[2]
    assert "function storeSaysKr(" in js, "storeSaysKr is gone — how is the cell rendered now?"

    cell = re.search(r'<td class="store-says">(.*?)</td>', js, re.DOTALL)
    assert cell is not None, "the store-says cell is gone — has the links table changed shape?"
    assert "storeSaysKr(" in cell.group(1), (
        "the store-says cell no longer renders through storeSaysKr, so the store's own "
        "measure is either missing or taken from the product's unit."
    )
    assert "unitKr(" not in cell.group(1), (
        "the store-says cell uses unitKr(), which labels with the PRODUCT's comparison unit "
        "— that states the wrong measure for every store printing in another one."
    )

    # The SECOND place a store's printed figure is drawn, and the one that shipped the bug
    # in prod: Fel & luckor's mismatch row. The first version of this gate covered only the
    # links panel and this row was left labelling with m.unit — it drew a Willys kr/kg as
    # "97,78 kr/st" on the very page whose job is to name what is wrong. Both halves or
    # neither: a rule that holds in one of two renderings of one number is not a rule.
    mismatch = re.search(r"butiken skriver <b>\$\{([^}]*(?:\}[^}]*)*?)\}</b>", js)
    assert mismatch is not None, "the mismatch row's printed figure is gone — has it moved?"
    assert "storeSaysKr(" in mismatch.group(1), (
        "Fel & luckor's 'butiken skriver' no longer renders through storeSaysKr. It must "
        "use the row's own printed_unit, which is None when nothing recorded a measure — "
        "labelling that with the product's unit is the bug this row exists to report."
    )
    assert "m.unit" not in mismatch.group(1), (
        "Fel & luckor's 'butiken skriver' labels the STORE's figure with the PRODUCT's "
        "unit (m.unit). Use m.printed_unit — the two are only equal when a measure was "
        "actually recorded, which is the minority of rows."
    )
