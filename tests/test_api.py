"""Tests for FastAPI admin endpoints and auth."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import Principal, get_principal
from domain.models import PricePoint, Product, ProductStore, Store
from domain.result import PriceExtractionResult

TENANT = "f21b6620-c793-46e3-a354-dfcd9956b4a2"
# Any well-formed UUID; the session is mocked, so nothing resolves it.
LINK_ID = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
CHECKED_AT = datetime(2026, 7, 14, 6, 0)


@pytest.fixture
def mock_session():
    """Mock async DB session, shaped like the real one.

    Session.add/add_all/expunge are SYNCHRONOUS on SQLAlchemy's AsyncSession — on a bare
    AsyncMock they return un-awaited coroutines (the RuntimeWarning noise every run
    carried), and production code that mistakenly wrote `await session.add(...)` would
    pass the whole suite.
    """
    session = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    session.expunge = MagicMock()
    return session


@pytest.fixture
def client(mock_session):
    """FastAPI TestClient with mocked auth (as the admin) and DB."""
    app = create_app()

    # get_principal is THE identity point: require_auth and the router's write gate both
    # resolve through it, so overriding it here covers both. Overriding require_auth alone
    # would leave the write gate live and 403 every POST in this file.
    async def override_principal():
        return Principal(email="test@example.com", is_admin=True)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_principal] = override_principal
    # Override the get_db used in admin router — it is injected via Depends(get_db)
    # We patch at the module level where get_db is defined
    from api.admin import get_db as admin_get_db

    app.dependency_overrides[admin_get_db] = override_get_db

    return TestClient(app)


@pytest.fixture
def mock_service(client):
    """Mocked PriceTrackerService wired into the app's dependency overrides."""
    from api.admin import get_price_tracker_service as admin_get_service

    service = MagicMock()

    product = MagicMock()
    product.id = "prod-1"
    service.create_product = AsyncMock(return_value=product)

    link = MagicMock()
    link.id = "link-1"
    service.link_product_store = AsyncMock(return_value=link)
    service.delete_product = AsyncMock(return_value=None)

    client.app.dependency_overrides[admin_get_service] = lambda: service
    return service


def _link_row(mock_session, product_store):
    """Point the mocked session's next `select(ProductStore)` at `product_store` (or None)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = product_store
    # The /frequency reschedule also runs the sibling-slot query (domain.schedule.
    # weekday_slot) on this same session; an empty row set makes it fall back to the
    # unstratified draw, which is all these endpoint tests need.
    result.all.return_value = []
    mock_session.execute.return_value = result


# --- Real ORM instances, deliberately NOT MagicMocks -------------------------------------
#
# The read-path tests below build REAL (transient, session-less) ORM objects. A MagicMock
# auto-creates every attribute it is asked for, so `product.package_size` on a mock returns a
# mock instead of raising — which is precisely why this suite stayed green for three plans
# while `list_products`, `get_product` and `get_price_history` were all reading columns the
# model no longer has. A real Product raises AttributeError. That is the whole point: these
# fixtures can fail, and against the pre-fix code they do.
#
# No database is involved — declarative objects construct fine without a session, and the
# session itself is still mocked.


def _product(unit: str = "st", name: str = "Lambi toalettpapper") -> Product:
    return Product(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(TENANT),
        name=name,
        brand="Lambi",
        category="Hushall",
        unit=unit,
    )


def _store(name: str = "Willys", slug: str = "willys") -> Store:
    return Store(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        store_type="grocery",
        base_url="https://www.willys.se",
    )


def _ps(
    product: Product,
    store: Store,
    *,
    package_size: str | None = "24-pack",
    package_quantity: str | None = "24",
    scraped_package_quantity: str | None = None,
) -> ProductStore:
    return ProductStore(
        id=uuid.uuid4(),
        product_id=product.id,
        store_id=store.id,
        store_url=f"https://www.willys.se/{uuid.uuid4()}",
        package_size=package_size,
        package_quantity=Decimal(package_quantity) if package_quantity is not None else None,
        scraped_package_quantity=(
            Decimal(scraped_package_quantity) if scraped_package_quantity is not None else None
        ),
        is_active=True,
        # Inherit the store schedule — the new normal state for a link.
        check_frequency_hours=None,
        check_weekdays=None,
        last_checked_at=None,
    )


def _pp(
    ps: ProductStore,
    *,
    price: str = "139.90",
    offer: str | None = None,
    store_unit: str | None = None,
    in_stock: bool = True,
) -> PricePoint:
    return PricePoint(
        id=uuid.uuid4(),
        product_store_id=ps.id,
        price_sek=Decimal(price),
        offer_price_sek=Decimal(offer) if offer is not None else None,
        store_unit_price_sek=Decimal(store_unit) if store_unit is not None else None,
        offer_type="kampanj" if offer is not None else None,
        offer_details=None,
        in_stock=in_stock,
        checked_at=CHECKED_AT,
    )


def _scalars(items):
    """A result whose .scalars().all() yields `items`."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _scalar(item):
    """A result whose .scalar_one_or_none() yields `item`."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = item
    return r


def _rows(items):
    """A result whose .all() yields `items` (a list of row tuples)."""
    r = MagicMock()
    r.all.return_value = items
    return r


def _extraction(
    *,
    price: str = "139.90",
    store_unit: str | None = "5.83",
    package_amount: str | None = None,
    package_unit: str | None = None,
    pack_size: int | None = None,
) -> PriceExtractionResult:
    return PriceExtractionResult(
        price_sek=Decimal(price),
        store_unit_price_sek=Decimal(store_unit) if store_unit is not None else None,
        offer_price_sek=None,
        offer_type=None,
        offer_details=None,
        in_stock=True,
        confidence=0.9,
        pack_size=pack_size,
        package_amount=Decimal(package_amount) if package_amount is not None else None,
        package_unit=package_unit,
        raw_response={},
    )


class TestPublicEndpoints:
    def test_legacy_admin_path_redirects_to_root(self, client):
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "/"

    def test_health_db_up(self, client):
        from unittest.mock import patch

        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("api.app.engine", mock_engine):
            r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] is True

    def test_health_db_down_returns_503(self, client):
        from unittest.mock import patch

        mock_engine = MagicMock()
        mock_engine.connect.side_effect = ConnectionError("db gone")

        with patch("api.app.engine", mock_engine):
            r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"
        assert r.json()["db"] is False


ADMIN_EMAIL = "magnus@example.com"
READER_EMAIL = "someone.else@example.com"


@pytest.fixture
def unmocked_client(monkeypatch):
    """A client with the REAL auth chain — no dependency overrides.

    Everything else in this file overrides get_principal; these tests are the ones that
    must not, because the role split is what they are checking.
    """
    monkeypatch.setenv("ALLOWED_ENTRA_EMAIL", ADMIN_EMAIL)
    return TestClient(create_app())


class TestAuth:
    def test_rejects_missing_header(self, unmocked_client):
        """No IAP header = the request did not come through the ingress at all."""
        assert unmocked_client.get("/").status_code == 403

    def test_fails_closed_when_no_admin_is_configured(self, monkeypatch):
        """An unconfigured instance grants nothing — not even reads."""
        monkeypatch.delenv("ALLOWED_ENTRA_EMAIL", raising=False)
        client = TestClient(create_app())
        r = client.get("/", headers={"X-Auth-Request-Email": ADMIN_EMAIL})
        assert r.status_code == 403

    def test_admin_email_is_matched_case_insensitively(self, unmocked_client):
        r = unmocked_client.get("/", headers={"X-Auth-Request-Email": ADMIN_EMAIL.upper()})
        assert r.status_code == 200
        assert "role-admin" in r.text


class TestIngressSecret:
    """Optional defense in depth: the email header is trusted verbatim, so anything
    that can open a TCP connection to the app port (a neighbour container on the
    docker network) can claim to be the admin. With INGRESS_SHARED_SECRET set, every
    request must also carry the secret the Traefik ingress injects — refused BEFORE
    the email is read. Unset = exactly the old behavior, so enabling it is an
    explicit two-sided move (env here, customRequestHeaders in home-server)."""

    def test_unset_secret_changes_nothing(self, unmocked_client):
        r = unmocked_client.get("/", headers={"X-Auth-Request-Email": ADMIN_EMAIL})
        assert r.status_code == 200

    def test_request_without_the_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ENTRA_EMAIL", ADMIN_EMAIL)
        monkeypatch.setenv("INGRESS_SHARED_SECRET", "s3cret")
        client = TestClient(create_app())
        r = client.get("/", headers={"X-Auth-Request-Email": ADMIN_EMAIL})
        assert r.status_code == 403

    def test_wrong_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ENTRA_EMAIL", ADMIN_EMAIL)
        monkeypatch.setenv("INGRESS_SHARED_SECRET", "s3cret")
        client = TestClient(create_app())
        r = client.get(
            "/",
            headers={"X-Auth-Request-Email": ADMIN_EMAIL, "X-Ingress-Auth": "wrong"},
        )
        assert r.status_code == 403

    def test_correct_secret_passes(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ENTRA_EMAIL", ADMIN_EMAIL)
        monkeypatch.setenv("INGRESS_SHARED_SECRET", "s3cret")
        client = TestClient(create_app())
        r = client.get(
            "/",
            headers={"X-Auth-Request-Email": ADMIN_EMAIL, "X-Ingress-Auth": "s3cret"},
        )
        assert r.status_code == 200
        assert "role-admin" in r.text


class TestReadOnlyRole:
    """Everyone the Entra gate let in may read; only ALLOWED_ENTRA_EMAIL may write.

    Membership is Entra's job (tenant + OAUTH2_PROXY_EMAIL_DOMAINS), so a second
    allowlist here would only be a second thing to forget to update.
    """

    def test_only_the_read_methods_are_safe(self):
        """The whole split hangs off this set. Adding a verb here opens every endpoint
        that uses it to every reader, with nothing else to notice."""
        from api.auth import SAFE_METHODS

        assert set(SAFE_METHODS) == {"GET", "HEAD", "OPTIONS"}

    def test_reader_may_load_the_portal(self, unmocked_client):
        r = unmocked_client.get("/", headers={"X-Auth-Request-Email": READER_EMAIL})
        assert r.status_code == 200
        assert "role-reader" in r.text

    def test_reader_may_read_the_logs(self, unmocked_client):
        """Reads are open to readers — /logs and /export included, by decision.

        /logs is the one read with no DB behind it, so it is the one this DB-less test can
        assert on end to end; the rest share the same router-level gate.
        """
        r = unmocked_client.get("/logs", headers={"X-Auth-Request-Email": READER_EMAIL})
        assert r.status_code == 200
        assert "logs" in r.json()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/products"),
            ("post", "/quick-add/preview"),
            ("post", f"/check/{LINK_ID}"),
            ("post", "/watches"),
            ("put", f"/products/{LINK_ID}"),
            ("delete", f"/products/{LINK_ID}"),
            ("delete", f"/product-stores/{LINK_ID}"),
        ],
    )
    def test_reader_may_not_write(self, unmocked_client, method, path):
        """The gate is keyed on the HTTP method, so it fires before any handler runs —
        no DB is touched and no store is fetched on a reader's behalf."""
        r = getattr(unmocked_client, method)(path, headers={"X-Auth-Request-Email": READER_EMAIL})
        assert r.status_code == 403

    def test_admin_passes_the_write_gate(self, unmocked_client):
        """422 (not 403) proves the gate let the admin through to body validation."""
        r = unmocked_client.post(
            "/products", json={}, headers={"X-Auth-Request-Email": ADMIN_EMAIL}
        )
        assert r.status_code == 422

    def test_reader_gets_no_write_controls_on_the_page(self, unmocked_client):
        """A button that can only 403 is worse than no button. The API is still the gate;
        this is about not offering the action."""
        reader = unmocked_client.get("/", headers={"X-Auth-Request-Email": READER_EMAIL}).text
        admin = unmocked_client.get("/", headers={"X-Auth-Request-Email": ADMIN_EMAIL}).text

        for control in ("showQuickAddModal()", "showCreateProductModal()", "showImportModal()"):
            assert control in admin
            # Present in the markup but hidden by the role-reader CSS rule, and the
            # server refuses the write regardless — assert the rule that does the hiding.
            assert "body.role-reader .admin-only" in reader

        assert 'class="btn btn-primary admin-only" onclick="showQuickAddModal()"' in reader
        assert "Läsbehörighet" in reader
        assert "Läsbehörighet" not in admin
        # Exportera is a GET — a reader keeps it.
        assert "loadExport()" in reader


class TestAdminDashboard:
    def test_dashboard_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Price Tracker" in r.text

    def test_dashboard_is_never_served_from_a_stale_cache(self, client):
        """Without Cache-Control the browser may keep a heuristically-cached page across
        deploys — after v0.45.0 a cached copy silently enforced the OLD input validation
        and read as 'the fix didn't work'. no-cache forces revalidation, and with no
        validator on the response, revalidation always fetches the current page."""
        r = client.get("/")
        assert r.headers.get("cache-control") == "no-cache"

    def test_no_admin_wording_reaches_the_user(self, client):
        """The 'Admin' naming was a holdover from the source platform — the app is just
        'Price Tracker' now. CSS comments may say what they like; visible text may not."""
        r = client.get("/")
        assert "<title>Price Tracker</title>" in r.text
        assert "Price Tracker Admin" not in r.text
        assert ">Admin<" not in r.text

    def test_sidebar_footer_shows_the_version(self, client):
        """The footer version comes from the same source of truth as the release tag
        (pyproject.toml / installed metadata), so what you see is what prod runs.

        Asserting THE resolved version, not any ``v\\d+`` pattern: the template's JS
        comments contain version literals, so a loose regex passes even with the
        injection deleted — which is exactly the silent rot this test exists to catch.
        """
        from api.admin import _APP_VERSION

        assert _APP_VERSION, "version must resolve from metadata or pyproject.toml"
        r = client.get("/")
        assert f"v{_APP_VERSION}" in r.text

    def test_sidebar_has_one_nav_item_per_page(self, client):
        """The sections are hash-routed pages picked from the left menu — a long
        product list must never push the buy list out of sight.

        Two groups since the 3a rebuild: VECKAN is what you act on this week, UNDERHÅLL is
        what keeps the data honest.
        """
        r = client.get("/")
        pages = ("kopa", "bevakningar", "utveckling", "produkter", "luckor", "loggar")
        for page in pages:
            assert f'data-page="{page}"' in r.text, f"nav/page missing for {page}"
            assert f'href="#/{page}"' in r.text
        # The page sections themselves exist for the router to toggle.
        assert r.text.count('class="app-page"') == len(pages)
        assert ">Veckan<" in r.text
        assert ">Underhåll<" in r.text

    def test_nav_counters_replace_the_global_stat_row(self, client):
        """The four numbers that used to sit above every page are the sidebar's counters
        now — a figure you never act on should not cost a band of vertical space on every
        screen. Fel & luckor's counter carries a severity: red when a SILENT error stands
        (we are showing a wrong number right now), amber when it is only gaps to close."""
        r = client.get("/")
        for counter in (
            "nav-count-kopa",
            "nav-count-bevakningar",
            "nav-count-produkter",
            "nav-count-luckor",
        ):
            assert f'id="{counter}"' in r.text
        assert 'class="stats-grid"' not in r.text
        assert ".nav-count.count-error" in r.text
        assert ".nav-count.count-gap" in r.text

    def test_schedule_is_store_level_and_hidden_from_add_flows(self, client):
        """The check schedule is a STORE property since v0.13.0: the add flows (quick-add,
        manual link) carry NO schedule fields — a new link inherits its store's schedule.
        Only the link-edit dialog offers an override, defaulting to the store standard."""
        r = client.get("/")
        # The add flows ask nothing about scheduling.
        assert 'id="qa-weekday"' not in r.text
        assert 'name="check_weekday"' not in r.text
        assert 'name="check_frequency_hours"' not in r.text
        # The edit dialog's override: store standard by default, weekday checkboxes for
        # a custom schedule (a LIST — Willys checks Mondays AND Fridays).
        assert 'id="edit-schedule-mode"' in r.text
        assert "Butikens standard" in r.text
        assert 'id="edit-weekdays"' in r.text

    def test_product_edit_dialog_exists_with_locked_unit(self, client):
        """Identity fields are editable from the product row; the unit is displayed but
        disabled — it is the scale of every link amount and the whole kr/unit history,
        so changing it means delete + recreate, never an edit."""
        r = client.get("/")
        assert 'id="modal-edit-product"' in r.text
        assert 'data-action="edit"' in r.text
        assert 'id="edit-product-unit" class="form-input" disabled' in r.text

    def test_link_dialog_edits_cadence(self, client):
        """Existing links' schedule override is editable (PUT /frequency has a UI):
        store standard by default, custom weekday checkboxes + interval behind it."""
        r = client.get("/")
        assert 'id="edit-schedule-mode"' in r.text
        assert 'id="edit-weekdays"' in r.text
        assert 'id="edit-frequency"' in r.text
        assert "/frequency'" in r.text  # the JS actually calls the endpoint

    def test_the_history_modal_answers_before_the_graph(self, client):
        """The modal used to open on a chart and a table and state nothing.

        Everything on the summary strip is a plain FACT about the fetched rows — a minimum,
        a maximum, a count, a timestamp. The one judgement, förändring, is READ off /stats
        and omitted when that payload is not loaded: change over a period is stats.py's
        definition, and a second one in JS would drift from it (Gotcha 4). The same shape
        as the deal row reading `verdict` instead of deciding it.
        """
        r = client.get("/")
        assert 'id="price-history-summary"' in r.text
        assert "function historySummaryHtml" in r.text
        # Read, never recomputed: the strip looks the row up by product_id.
        assert (
            "statsData && statsData.products || []).find(p => p.product_id === productId)" in r.text
        )
        assert "changeCell(statsRow.change_pct)" in r.text

    def test_the_history_chart_marks_campaigns_and_drops_the_redundant_line(self, client):
        """A campaign is the reason a curve dips, and the dip alone does not say so — the
        point is drawn as a hollow ring instead.

        The "Billigast tillgänglig" line is gone: it traced the minimum across links, which
        is by construction whichever coloured line sits lowest at each moment, so it added
        nothing while being the darkest and thickest mark on the canvas. A dashed mean took
        its place. buildBestAvailableSeries stays — it is the honest carry-forward walk, and
        an envelope should reuse it rather than grow a second copy.
        """
        r = client.get("/")
        assert "is_offer" in r.text
        assert "pointRadius: offers.map(" in r.text
        # The DATASET, not the prose: the comment above it still names what was removed
        # and why, which is the part a future reader needs.
        assert "role: 'best'" not in r.text
        assert "label: 'Billigast tillgänglig'" not in r.text
        assert "function buildBestAvailableSeries" in r.text
        assert "Snitt av observationerna" in r.text
        # Notation belongs in the caption, not in a legend row you could switch off.
        assert "Ringar är erbjudanden" in r.text

    def test_the_history_table_prints_swedish_money(self, client):
        """It was the one table in the portal that bypassed kr()/unitKr() and concatenated
        the raw number, so it printed "79.9 kr" — an English decimal point and a dropped
        öre — beside cells that said "79,90 kr" everywhere else."""
        r = client.get("/")
        assert "escapeHtml(h.price_sek + ' kr')" not in r.text
        assert "escapeHtml(h.offer_price_sek + ' kr')" not in r.text
        assert "escapeHtml(kr(h.price_sek))" in r.text
        assert "escapeHtml(unitKr(h.unit_price_sek, unit))" in r.text
        # The tooltip had the same slip.
        assert "item.parsed.y.toFixed(2)" not in r.text

    def test_deals_view_is_a_decision_not_a_listing(self, client):
        """Att köpa must carry the platform's OWN comparison, not the store's framing.

        The margin is `savings_per_unit_sek / best_alt_unit_price_sek` — a PERCENTAGE,
        because kronor per unit cannot be compared between rows — and it is what the list
        is ORDERED by. Since v0.53.1 it is no longer PRINTED on a best row: every row under
        "Värt att köpa" is best by construction, the heading says so, and a list expresses
        its ranking by being a list. `discount_percent` must never be the ranking: 30 % off
        a bad price is still a bad price.

        The other two verdicts keep their sentence, because nothing else carries it — WORSE
        is the reason the row was demoted (the span bar is TIME; it cannot say another link
        is cheaper right now) and UNKNOWN names the gap plus its fix.
        """
        r = client.get("/")
        assert "dealMarginPct" in r.text
        assert "saving / d.best_alt_unit_price_sek" in r.text
        assert "dealVerdictHtml" in r.text
        assert "if (kind === DEAL_BEST) return '';" in r.text
        assert "dyrare per" in r.text
        assert "går inte att rangordna" in r.text
        # The butik moved out of that sentence and into the meta line — it is the way OUT
        # to the store's own page and must not have gone with the sentence.
        assert "deal-store" in r.text
        # The span bar is the row's OTHER question — right moment, not right store.
        assert "Prisläge i eget spann" in r.text
        assert "inget spann än" in r.text

    def test_the_buy_list_is_ranked_on_margin_not_on_recency(self, client):
        """Recency says when WE looked; it is our schedule's business, not a reason to put
        one campaign above another. It survives as a line of metadata."""
        r = client.get("/")
        assert "function rankDeals" in r.text
        assert "dealMarginPct(b) || 0) - (dealMarginPct(a) || 0" in r.text

    def test_store_names_link_to_the_store_page(self, client):
        """Store names in the deals and links views are ways IN to the store's page —
        the shopping-list use case dies without them."""
        r = client.get("/")
        assert "storeLinkHtml" in r.text
        assert 'target="_blank" rel="noopener"' in r.text

    def test_product_table_shows_store_count_not_names(self, client):
        """The Butiker column is a COUNT: linked store names swallowed the whole table,
        and the names already live behind the Länkar button (distinct display names, so
        two pack sizes at one butik count once)."""
        r = client.get("/")
        assert "new Set(links.map(s => s.store_name)).size" in r.text

    def test_freshness_line_exists(self, client):
        """The weekly rhythm made visible: is the scheduler running, when did it last look,
        when is the next round. It lives in the sidebar foot — visible on every page instead
        of only on the one it happened to be attached to."""
        r = client.get("/")
        assert 'id="sched-state"' in r.text
        assert 'id="sched-when"' in r.text
        assert "Schemaläggare aktiv" in r.text
        assert "'Senast '" in r.text and "'nästa '" in r.text

    def test_page_is_mobile_ready(self, client):
        """The app doubles as the in-store shopping list: viewport meta, a responsive
        breakpoint, and sideways-scrolling table containers (never the page)."""
        r = client.get("/")
        assert 'name="viewport"' in r.text
        assert "@media (max-width: 768px)" in r.text
        assert "overflow-x: auto" in r.text

    def test_faults_and_gaps_separates_silent_errors_from_gaps(self, client):
        """The page's whole reason to exist. A gap announces itself; a SILENT error does
        not — the check says "ok", the price is plausible, and nothing else in the UI would
        ever mention it. So the silent errors sit at the top and in red, and the amber gaps
        below. And nothing here may be dismissible: no flag is persisted, so a dismiss
        button would only hide an error that is still running."""
        r = client.get("/")
        assert "Tysta fel" in r.text
        assert "Extraktionen har fallit ner en pinne" in r.text
        assert "Jämförpriset går inte ihop" in r.text
        assert "Länkar utan mängd" in r.text
        assert "Trasiga länkar" in r.text
        # The consequence text is derived from the expected source, never invented per row.
        assert "TIER_IMPACT" in r.text
        # A wall is the store's state, not ours — without this line an ICA block reads as a
        # breakdown and invites deleting perfectly good links.
        assert "Blockerade hämtningar räknas inte som fel här" in r.text
        assert 'data-issue-action="save-amount"' in r.text
        assert 'data-issue-action="dismiss"' not in r.text
        assert "Kvittera" not in r.text

    def test_jfr_pris_is_the_display_term(self, client):
        """Stores print 'jfr-pris' on the shelf label — the UI uses the shelf's word.
        (Value labels like 'kr/st' stay; this is about the HEADINGS.)"""
        r = client.get("/")
        assert "jfr-pris" in r.text
        assert "Lägsta kr/enhet" not in r.text

    def test_buy_list_is_the_start_page(self, client):
        """Att köpa first in the menu and the default page: the question the app exists to
        answer opens on load; the long product list is one click away. The pre-3a hashes
        still resolve — a bookmark is a promise."""
        r = client.get("/")
        # Menu order: Att köpa above Produkter.
        assert r.text.index('href="#/kopa"') < r.text.index('href="#/produkter"')
        # The server-rendered breadcrumb matches the client router's fallback.
        assert 'id="breadcrumb-current">Att köpa' in r.text
        assert "return 'kopa';" in r.text  # currentPage() fallback
        assert "erbjudanden: 'kopa'" in r.text
        assert "statistik: 'utveckling'" in r.text

    def test_numbers_are_swedish_and_absence_is_never_zero(self, client):
        """Money always two decimals with a comma, percentages with a real minus sign, and
        a missing value as an em dash — never a 0, which would be a confident claim about
        something we did not observe."""
        r = client.get("/")
        assert "new Intl.NumberFormat('sv-SE'" in r.text
        assert "minimumFractionDigits: 2" in r.text
        assert "const DASH = '\\u2014'" in r.text
        assert "'\\u2212'" in r.text  # U+2212 MINUS SIGN, not a hyphen


class TestStoresEndpoints:
    def test_list_stores(self, client, mock_session):
        mock_store = MagicMock()
        mock_store.id = "store-1"
        mock_store.name = "Willys"
        mock_store.slug = "willys"
        mock_store.store_type = "grocery"
        mock_store.base_url = "https://www.willys.se"
        mock_store.is_active = True

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_store]
        mock_session.execute.return_value = mock_result

        r = client.get("/stores")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["slug"] == "willys"


class TestProductsEndpoints:
    def test_create_product(self, client, mock_service):
        r = client.post(
            "/products",
            json={
                "tenant_id": TENANT,
                "name": "Test Product",
                "brand": "TestBrand",
                "category": "TestCat",
                "unit": "st",
            },
        )
        assert r.status_code == 201
        assert r.json()["product_id"] == "prod-1"

        kwargs = mock_service.create_product.await_args.kwargs
        assert kwargs["unit"] == "st"

    def test_create_product_does_not_persist_package_fields(self, client, mock_service):
        """Package data belongs to the LINK. A stale client still sending it must not smuggle
        it onto the product — Pydantic ignores unknown fields, so assert on the SERVICE CALL,
        not on a 422 that will never come.
        """
        r = client.post(
            "/products",
            json={
                "tenant_id": TENANT,
                "name": "Lambi toalettpapper",
                "unit": "st",
                "package_size": "24-pack",
                "package_quantity": 24,
            },
        )
        assert r.status_code == 201

        kwargs = mock_service.create_product.await_args.kwargs
        assert "package_size" not in kwargs
        assert "package_quantity" not in kwargs

    def test_list_products(self, client, mock_session):
        mock_product = MagicMock()
        mock_product.id = "prod-1"
        mock_product.name = "Mjolk"
        mock_product.brand = "Arla"
        mock_product.category = "Mejeri"
        mock_product.unit = "ml"
        mock_product.package_size = None
        mock_product.package_quantity = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_product]
        mock_session.execute.return_value = mock_result

        r = client.get("/products")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "Mjolk"

    def test_delete_product(self, client, mock_service):
        r = client.delete("/products/prod-1")
        assert r.status_code == 200
        assert r.json()["message"] == "Product deleted successfully"

    def _updatable(self, mock_session):
        """A found product behind PUT /products/{id} — returns the mutable mock."""
        product = MagicMock()
        product.unit = "st"
        result = MagicMock()
        result.scalar_one_or_none.return_value = product
        mock_session.execute.return_value = result
        return product

    def test_update_product_edits_identity_fields(self, client, mock_session):
        product = self._updatable(mock_session)
        r = client.put(
            f"/products/{uuid.uuid4()}",
            json={"name": "  Nytt namn  ", "brand": "", "category": "Mejeri"},
        )
        assert r.status_code == 200
        assert product.name == "Nytt namn"  # trimmed
        assert product.brand is None  # '' clears an optional field
        assert product.category == "Mejeri & Ost"  # legacy free text → canonical section

    def test_update_product_cannot_change_unit(self, client, mock_session):
        """Unit is LOCKED: every link amount and the whole kr/unit history are expressed
        in it. A client sending unit anyway must be ignored (delete + recreate is the way)."""
        product = self._updatable(mock_session)
        r = client.put(f"/products/{uuid.uuid4()}", json={"name": "Namn", "unit": "kg"})
        assert r.status_code == 200
        assert product.unit == "st"

    def test_update_product_blank_name_is_400(self, client, mock_session):
        product = self._updatable(mock_session)
        r = client.put(f"/products/{uuid.uuid4()}", json={"name": "   "})
        assert r.status_code == 400
        # The endpoint rejected before assigning — the mock attribute was never set to a str.
        assert not isinstance(product.name, str)

    def test_update_product_unknown_id_is_404(self, client, mock_session):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = result
        r = client.put(f"/products/{uuid.uuid4()}", json={"name": "Namn"})
        assert r.status_code == 404


class TestProductStoreLinkEndpoints:
    """The link owns the packaging, and it is addressed by its own id — never by the
    (product_id, store_id) pair, which stopped being unique when uq_product_store was dropped.
    """

    def test_link_store_carries_the_package_fields(self, client, mock_service):
        r = client.post(
            "/products/prod-1/stores",
            json={
                "store_id": "store-1",
                "store_url": "https://www.willys.se/lambi-24p",
                "check_frequency_hours": 72,
                "package_size": "24-pack",
                "package_quantity": 24,
            },
        )
        assert r.status_code == 201

        kwargs = mock_service.link_product_store.await_args.kwargs
        assert kwargs["package_size"] == "24-pack"
        assert float(kwargs["package_quantity"]) == 24.0

    def test_link_second_pack_size_at_the_same_store_is_accepted(self, client, mock_service):
        """The phase's own acceptance scenario: an 8-pack beside the 24-pack, same store."""
        for url, label, qty in (
            ("https://www.willys.se/lambi-24p", "24-pack", 24),
            ("https://www.willys.se/lambi-8p", "8-pack", 8),
        ):
            r = client.post(
                "/products/prod-1/stores",
                json={
                    "store_id": "store-1",
                    "store_url": url,
                    "check_frequency_hours": 72,
                    "package_size": label,
                    "package_quantity": qty,
                },
            )
            assert r.status_code == 201, r.text

        assert mock_service.link_product_store.await_count == 2

    @pytest.mark.parametrize("bad_quantity", [0, -1, -0.5])
    def test_link_store_rejects_non_positive_package_quantity(
        self, client, mock_service, bad_quantity
    ):
        """The >0 check MOVED from create_product to the link; it was not dropped (ASVS V5)."""
        r = client.post(
            "/products/prod-1/stores",
            json={
                "store_id": "store-1",
                "store_url": "https://www.willys.se/lambi",
                "check_frequency_hours": 72,
                "package_quantity": bad_quantity,
            },
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        # The rejection must name the offending field (the field name is the wire contract and
        # stays verbatim in the Swedish copy) and say why.
        assert "package_quantity" in detail
        assert "positiv" in detail
        mock_service.link_product_store.assert_not_awaited()

    def test_link_store_duplicate_url_returns_409_not_500(self, client, mock_service):
        """store_url is globally unique now, so pasting a tracked URL is a normal user action.
        It must be a curated 409 — not a 500 leaking a driver message.
        """
        from sqlalchemy.exc import IntegrityError

        mock_service.link_product_store = AsyncMock(
            side_effect=IntegrityError(
                "INSERT INTO product_stores ...",
                {},
                Exception(
                    'duplicate key value violates unique constraint "uq_product_stores_store_url"'
                ),
            )
        )

        r = client.post(
            "/products/prod-1/stores",
            json={
                "store_id": "store-1",
                "store_url": "https://www.willys.se/lambi-24p",
                "check_frequency_hours": 72,
            },
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "bevakas redan" in detail
        # The driver's message must not reach the client.
        assert "duplicate key" not in detail
        assert "uq_product_stores_store_url" not in detail


class TestLinkFrequencyEndpoint:
    """PUT /frequency sets the link's schedule OVERRIDE — or clears it back to inherit."""

    @staticmethod
    def _link(**kwargs):
        # MagicMock with REAL schedule values: the endpoint resolves the effective
        # schedule through domain.schedule, and truthy mock attributes would
        # masquerade as weekday lists.
        ps = MagicMock()
        ps.check_weekdays = kwargs.get("check_weekdays")
        ps.check_frequency_hours = kwargs.get("check_frequency_hours")
        ps.store.check_weekdays = kwargs.get("store_weekdays")
        ps.store.check_frequency_hours = kwargs.get("store_frequency", 72)
        return ps

    def test_update_weekdays_override(self, client, mock_session):
        ps = self._link()
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_weekdays": [0, 4]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["message"] == "Frequency updated"
        assert ps.check_weekdays == [0, 4]
        assert ps.check_frequency_hours is None
        assert r.json()["next_check_at"] is not None

    def test_update_interval_override(self, client, mock_session):
        ps = self._link()
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_frequency_hours": 168},
        )
        assert r.status_code == 200, r.text
        assert ps.check_frequency_hours == 168
        assert ps.check_weekdays is None

    def test_both_null_clears_back_to_store_schedule(self, client, mock_session):
        """The reset path: a link with an override returns to following its store."""
        ps = self._link(check_weekdays=[2], store_weekdays=[0])
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_frequency_hours": None, "check_weekdays": None},
        )
        assert r.status_code == 200, r.text
        assert ps.check_weekdays is None
        assert ps.check_frequency_hours is None
        # next_check_at is rescheduled from the STORE's schedule (Mondays here).
        assert r.json()["next_check_at"] is not None

    def test_update_frequency_unknown_link_returns_404(self, client, mock_session):
        _link_row(mock_session, None)
        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_frequency_hours": 168},
        )
        assert r.status_code == 404

    def test_update_frequency_malformed_uuid_returns_400(self, client):
        r = client.put(
            "/product-stores/not-a-uuid/frequency",
            json={"check_frequency_hours": 168},
        )
        assert r.status_code == 400

    def test_update_frequency_rejects_out_of_range_hours(self, client):
        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_frequency_hours": 24},
        )
        assert r.status_code == 400

    def test_update_frequency_rejects_out_of_range_weekday(self, client):
        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_weekdays": [9]},
        )
        assert r.status_code == 400


class TestLinkPackagingEndpoint:
    def test_update_packaging_by_link_id(self, client, mock_session):
        ps = MagicMock()
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/packaging",
            json={"package_size": "8-pack", "package_quantity": 8},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["package_size"] == "8-pack"
        assert body["package_quantity"] == 8.0

    def test_update_packaging_unknown_link_returns_404(self, client, mock_session):
        _link_row(mock_session, None)
        r = client.put(
            f"/product-stores/{LINK_ID}/packaging",
            json={"package_size": "8-pack", "package_quantity": 8},
        )
        assert r.status_code == 404

    def test_update_packaging_malformed_uuid_returns_400(self, client):
        r = client.put(
            "/product-stores/not-a-uuid/packaging",
            json={"package_quantity": 8},
        )
        assert r.status_code == 400

    @pytest.mark.parametrize("bad_quantity", [0, -3])
    def test_update_packaging_rejects_non_positive_quantity(
        self, client, mock_session, bad_quantity
    ):
        ps = MagicMock()
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/packaging",
            json={"package_quantity": bad_quantity},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "package_quantity" in detail
        assert "positiv" in detail

    def test_packaging_body_cannot_repoint_the_link(self, client, mock_session):
        """store_url is the link's identity — re-pointing it would rewrite the meaning of its
        entire price history. The schema must ignore it, never apply it.
        """
        from api.schemas import ProductStoreUpdate

        assert "store_url" not in ProductStoreUpdate.model_fields
        assert "store_id" not in ProductStoreUpdate.model_fields

        ps = MagicMock()
        ps.store_url = "https://www.willys.se/lambi-24p"
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/packaging",
            json={
                "package_size": "8-pack",
                "package_quantity": 8,
                "store_url": "https://www.willys.se/something-else",
            },
        )
        assert r.status_code == 200
        assert ps.store_url == "https://www.willys.se/lambi-24p"


class TestLinkDeleteEndpoint:
    def test_delete_link_by_id(self, client, mock_session):
        ps = MagicMock()
        _link_row(mock_session, ps)

        r = client.delete(f"/product-stores/{LINK_ID}")
        assert r.status_code == 200
        assert r.json()["message"] == "Product unlinked from store successfully"
        mock_session.delete.assert_awaited_once_with(ps)

    def test_delete_unknown_link_returns_404(self, client, mock_session):
        _link_row(mock_session, None)
        r = client.delete(f"/product-stores/{LINK_ID}")
        assert r.status_code == 404

    def test_delete_link_malformed_uuid_returns_400(self, client):
        r = client.delete("/product-stores/not-a-uuid")
        assert r.status_code == 400


class TestOldPairKeyedRoutesAreGone:
    """The old paths were unfixable by construction: the path IS the ambiguous key."""

    def test_old_frequency_route_is_unregistered(self, client):
        r = client.put(
            "/products/prod-1/stores/store-1/frequency",
            json={"check_frequency_hours": 168},
        )
        assert r.status_code == 404

    def test_old_unlink_route_is_unregistered(self, client):
        r = client.delete("/products/prod-1/stores/store-1")
        assert r.status_code == 404


class TestComputedUnitPriceOnRead:
    """Every unit price the API reports is COMPUTED from the link's own quantity (D-03/D-04).

    The store's printed one travels beside it in a SEPARATE key and is never the same number
    by definition (stores print kr/rulle, kr/pack and kr/100g interchangeably), so a test that
    accepted either would prove nothing.
    """

    def test_list_products_computes_unit_price_from_the_link(self, client, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90", store_unit="5.83")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            _rows([]),  # broken-links query (domain/link_health.py): no attempts recorded
        ]

        r = client.get("/products")
        assert r.status_code == 200
        link = r.json()[0]["stores"][0]

        # 139.90 / 24 = 5.829166… → 5.83 at the presentation boundary.
        assert link["unit_price_sek"] == pytest.approx(5.83)
        assert link["store_unit_price_sek"] == pytest.approx(5.83)  # a SEPARATE key
        assert "store_unit_price_sek" in link and "unit_price_sek" in link
        assert link["package_size"] == "24-pack"
        assert link["package_quantity"] == pytest.approx(24.0)
        assert link["needs_amount"] is False
        assert link["quantity_mismatch"] is False

    def test_list_products_sets_needs_amount_on_a_link_without_a_quantity(
        self, client, mock_session
    ):
        """A NULL quantity is a visible flag (D-02) — not a zero, not a crash."""
        product, store = _product(), _store()
        ps = _ps(product, store, package_size=None, package_quantity=None)
        pp = _pp(ps, price="129.00", store_unit="12.90")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            _rows([]),  # broken-links query (domain/link_health.py): no attempts recorded
        ]

        r = client.get("/products")
        assert r.status_code == 200
        link = r.json()[0]["stores"][0]

        assert link["unit_price_sek"] is None
        assert link["needs_amount"] is True
        # The store still printed something; it is simply not comparable.
        assert link["store_unit_price_sek"] == pytest.approx(12.90)

    def test_list_products_flags_a_quantity_mismatch_without_adopting_the_page_value(
        self, client, mock_session
    ):
        """D-07/D-09: the page says 12, the operator typed 24. The API reports 24 and flags it.

        Presenting the scraped value as if it were the stored one would be the silent-rewrite
        this phase exists to prevent — every kr/unit in the app is computed from that number.
        """
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24", scraped_package_quantity="12")
        pp = _pp(ps, price="139.90")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            _rows([]),  # broken-links query (domain/link_health.py): no attempts recorded
        ]

        r = client.get("/products")
        link = r.json()[0]["stores"][0]

        assert link["quantity_mismatch"] is True
        assert link["package_quantity"] == pytest.approx(24.0)  # STILL the operator's value
        assert link["scraped_package_quantity"] == pytest.approx(12.0)
        # ...and the unit price is computed from 24, not from the page's 12.
        assert link["unit_price_sek"] == pytest.approx(5.83)

    def test_the_offer_price_wins_the_unit_price_computation(self, client, mock_session):
        """The effective price is what you actually pay: the offer when there is one."""
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90", offer="119.90")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            _rows([]),  # broken-links query (domain/link_health.py): no attempts recorded
        ]

        r = client.get("/products")
        link = r.json()[0]["stores"][0]

        assert link["unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)  # ~5.00
        assert link["unit_price_sek"] != pytest.approx(139.90 / 24, rel=1e-3)  # not the regular


class TestBrokenLinkFlag:
    """v0.40.0: is_broken/broken_detail on every link row — the 'Trasig länk' facet's and
    the links-panel badge's shared source. THE judgement is domain/link_health.py; these
    tests pin that it reaches the wire."""

    def test_list_products_flags_a_link_with_a_failure_streak(self, client, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            # The broken-links query: the link's three most recent non-blocked attempts.
            _rows([(ps.id, "fetch_failed", "HTTP 404")] * 3),
        ]

        r = client.get("/products")
        assert r.status_code == 200
        link = r.json()[0]["stores"][0]

        assert link["is_broken"] is True
        assert link["broken_detail"] == "HTTP 404"

    def test_a_healthy_link_carries_the_flag_as_false(self, client, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90")
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store)]),
            _scalars([pp]),
            _rows([(ps.id, "ok", None), (ps.id, "fetch_failed", "HTTP 404")]),
        ]

        r = client.get("/products")
        link = r.json()[0]["stores"][0]

        assert link["is_broken"] is False
        assert link["broken_detail"] is None

    def test_get_product_carries_the_computed_price_and_both_flags(self, client, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="8", scraped_package_quantity="8")
        pp = _pp(ps, price="59.90", store_unit="8.10")
        mock_session.execute.side_effect = [
            _scalar(product),
            _rows([(ps, store)]),
            _scalar(pp),
            _rows([]),  # broken-links query: no attempts recorded
        ]

        r = client.get(f"/products/{product.id}")
        assert r.status_code == 200
        link = r.json()["stores"][0]

        assert link["unit_price_sek"] == pytest.approx(7.49)  # computed: 59.90 / 8
        assert link["store_unit_price_sek"] == pytest.approx(8.10)  # what the store printed
        assert link["needs_amount"] is False
        assert link["quantity_mismatch"] is False  # 8 == 8

    def test_price_history_computes_the_unit_price_and_keeps_the_printed_one(
        self, client, mock_session
    ):
        """The history response could not even be CONSTRUCTED before this plan: it read a
        dropped column and omitted a required field. Only a mocked service kept it green.
        """
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90", store_unit="5.83")
        mock_session.execute.side_effect = [
            _rows([(pp, ps, store)])
        ]  # (point, link, store) — price_history_rows tuple order

        r = client.get(f"/products/{product.id}/prices")
        assert r.status_code == 200
        row = r.json()[0]

        assert row["unit_price_sek"] == pytest.approx(5.83)
        assert row["store_unit_price_sek"] == pytest.approx(5.83)
        assert row["in_stock"] is True


class TestProductLinksEndpoint:
    """GET /products/{id}/links — the product page's data source (D-12)."""

    def test_links_endpoint_preserves_the_services_ranking(self, client, mock_service):
        """Cheapest-per-unit first, "needs amount" last. The endpoint RENDERS that order and
        must never re-sort: the domain owns the one unit-price definition, and a second sort
        here is how a second definition gets in.
        """
        ranked = [
            {
                "product_store_id": "l1",
                "store_name": "Willys",
                "package_size": "24-pack",
                "unit_price_sek": 5.83,
                "needs_amount": False,
                "quantity_mismatch": False,
            },
            {
                "product_store_id": "l2",
                "store_name": "ICA",
                "package_size": "8-pack",
                "unit_price_sek": 7.49,
                "needs_amount": False,
                "quantity_mismatch": False,
            },
            {
                "product_store_id": "l3",
                "store_name": "Coop",
                "package_size": None,
                "unit_price_sek": None,
                "needs_amount": True,
                "quantity_mismatch": False,
            },
        ]
        mock_service.get_links_for_product = AsyncMock(return_value=ranked)

        r = client.get(f"/products/{uuid.uuid4()}/links")
        assert r.status_code == 200
        rows = r.json()

        assert [row["product_store_id"] for row in rows] == ["l1", "l2", "l3"]
        assert rows[0]["unit_price_sek"] < rows[1]["unit_price_sek"]
        assert rows[-1]["needs_amount"] is True  # the amount-less link sank to the bottom

    def test_links_endpoint_rejects_a_malformed_product_id(self, client):
        r = client.get("/products/not-a-uuid/links")
        assert r.status_code == 400


class TestStoresArrayIsRanked:
    """`stores` comes back cheapest-per-unit first, amount-less links last — on BOTH routes.

    Neither route had an ORDER BY: the array arrived in Postgres' arbitrary row order, and the
    frontend read it as if position meant something. These tests feed the links in the WORST
    order (dearest first, the amount-less one at the front) and require the response to fix it,
    so a regression to "whatever the DB handed back" fails here.
    """

    def _three_links(self):
        """A 24-pack (cheap/unit), an 8-pack (dear/unit) and a link with no amount at all."""
        product = _product()
        willys, ica, coop = (
            _store("Willys", "willys"),
            _store("ICA", "ica"),
            _store("Coop", "coop"),
        )
        cheap = _ps(product, willys, package_size="24-pack", package_quantity="24")
        dear = _ps(product, ica, package_size="8-pack", package_quantity="8")
        amountless = _ps(product, coop, package_size=None, package_quantity=None)
        return (
            product,
            # 139.90/24 = 5.83   |   59.90/8 = 7.49   |   no amount -> no kr/unit
            [
                (amountless, coop, _pp(amountless, price="19.90")),
                (dear, ica, _pp(dear, price="59.90")),
                (cheap, willys, _pp(cheap, price="139.90")),
            ],
        )

    def test_list_products_ranks_the_links(self, client, mock_session):
        product, rows = self._three_links()
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store) for ps, store, _ in rows]),
            _scalars([pp for _, _, pp in rows]),
            _rows([]),  # broken-links query: no attempts recorded
        ]

        r = client.get("/products")
        assert r.status_code == 200
        links = r.json()[0]["stores"]

        assert [link["store_name"] for link in links] == ["Willys", "ICA", "Coop"]
        assert links[0]["unit_price_sek"] == pytest.approx(5.83)
        assert links[1]["unit_price_sek"] == pytest.approx(7.49)
        # Last, despite being the cheapest ABSOLUTE price (19.90) — it has no kr/unit at all.
        assert links[-1]["needs_amount"] is True
        assert links[-1]["unit_price_sek"] is None

    def test_get_product_ranks_the_links(self, client, mock_session):
        product, rows = self._three_links()
        # The detail route fetches the latest price point per link, one query each.
        mock_session.execute.side_effect = [
            _scalar(product),
            _rows([(ps, store) for ps, store, _ in rows]),
            *[_scalar(pp) for _, _, pp in rows],
            _rows([]),  # broken-links query: no attempts recorded
        ]

        r = client.get(f"/products/{product.id}")
        assert r.status_code == 200
        links = r.json()["stores"]

        assert [link["store_name"] for link in links] == ["Willys", "ICA", "Coop"]
        assert links[-1]["needs_amount"] is True

    def test_the_offer_price_decides_the_rank(self, client, mock_session):
        """Ranking runs on the EFFECTIVE price. A rea that undercuts a rival must reorder them,
        or the list recommends the wrong pack for as long as the offer lasts.
        """
        product = _product()
        willys, ica = _store("Willys", "willys"), _store("ICA", "ica")
        big = _ps(product, willys, package_size="24-pack", package_quantity="24")
        small = _ps(product, ica, package_size="8-pack", package_quantity="8")
        rows = [
            # 24-pack at full price: 139.90/24 = 5.83/st
            (big, willys, _pp(big, price="139.90")),
            # 8-pack on rea: 39.90/8 = 4.99/st — cheaper per unit than the big pack
            (small, ica, _pp(small, price="59.90", offer="39.90")),
        ]
        mock_session.execute.side_effect = [
            _scalars([product]),
            _rows([(ps, store) for ps, store, _ in rows]),
            _scalars([pp for _, _, pp in rows]),
            _rows([]),  # broken-links query: no attempts recorded
        ]

        r = client.get("/products")
        links = r.json()[0]["stores"]

        assert [link["store_name"] for link in links] == ["ICA", "Willys"]
        assert links[0]["unit_price_sek"] == pytest.approx(4.99)


class TestDealsEndpoints:
    def test_get_deals(self, client, mock_session):
        # Empty deals — just verify endpoint responds
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        r = client.get("/deals")
        assert r.status_code == 200
        assert r.json() == []

    def test_deals_two_links_same_store(self, client, mock_session):
        """Two pack sizes at ONE store are TWO deals, not one.

        The pre-phase dedupe key was the Python-side `(product.id, store.id)` tuple, which was
        single-valued only because of the constraint this phase dropped. Fed two links at one
        store it kept an ARBITRARY one and silently discarded the other — no error, no 500,
        one pack size simply missing from the operator's deals list. That is why this needs a
        behavioral test: 04.1-04's static gate detects a `select()` constrained on both
        columns and structurally cannot see a Python tuple key.
        """
        product, store = _product(), _store()
        ps24 = _ps(product, store, package_size="24-pack", package_quantity="24")
        ps8 = _ps(product, store, package_size="8-pack", package_quantity="8")
        pp24 = _pp(ps24, price="139.90", offer="119.90")
        pp8 = _pp(ps8, price="59.90", offer="49.90")

        mock_session.execute.side_effect = [
            _rows(
                [
                    (pp24, ps24, product, store),
                    (pp8, ps8, product, store),
                ]
            ),
            # The alternatives query: latest point per link across the product's links.
            _rows(
                [
                    (ps24, store, pp24),
                    (ps8, store, pp8),
                ]
            ),
            # The link-health query (v0.50.0): no attempts recorded, nothing broken.
            _rows([]),
            # The floor query (every point in the window). Coherent with the rows above:
            # the 24-pack's 5,00 kr/st IS the product's floor, so it buys at a good moment
            # while the 8-pack sits 24 % above it.
            _rows([(pp24, ps24, store), (pp8, ps8, store)]),
        ]

        r = client.get("/deals")
        assert r.status_code == 200
        deals = r.json()

        assert len(deals) == 2, "the two pack sizes at one store collapsed into one deal row"
        assert {d["package_size"] for d in deals} == {"24-pack", "8-pack"}
        assert all(d["store_name"] == "Willys" for d in deals)

        by_pack = {d["package_size"]: d for d in deals}
        assert by_pack["24-pack"]["unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)
        assert by_pack["8-pack"]["unit_price_sek"] == pytest.approx(49.90 / 8, rel=1e-3)

        # Each deal's best alternative is the product's OTHER link — including a different
        # pack size at the SAME store: the decision is "cheapest way to buy the good".
        assert by_pack["24-pack"]["best_alt_package_size"] == "8-pack"
        assert by_pack["24-pack"]["best_alt_unit_price_sek"] == pytest.approx(49.90 / 8, rel=1e-3)
        assert by_pack["8-pack"]["best_alt_package_size"] == "24-pack"
        assert by_pack["8-pack"]["best_alt_unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)

        # THE verdict (domain/deals.py) rides on the wire since v0.41.0 — the portal and
        # the weekly email read it instead of re-deriving the comparison.
        assert by_pack["24-pack"]["verdict"] == "best"
        assert by_pack["24-pack"]["savings_per_unit_sek"] == pytest.approx(
            49.90 / 8 - 119.90 / 24, rel=1e-2
        )
        assert by_pack["8-pack"]["verdict"] == "worse"
        assert by_pack["8-pack"]["savings_per_unit_sek"] < 0

    def test_deals_are_still_ordered_by_recency(self, client, mock_session):
        """The computed kr/unit is EXPOSED on each deal, not adopted as the sort key.
        Re-ranking deals is a behavior change to an unrelated feature.
        """
        product, store = _product(), _store()
        cheap = _ps(product, store, package_size="24-pack", package_quantity="24")
        pricey = _ps(product, store, package_size="8-pack", package_quantity="8")
        # The pricier-per-unit link is the more RECENT row, so it must come first.
        pp_pricey = _pp(pricey, price="59.90", offer="49.90")
        pp_cheap = _pp(cheap, price="139.90", offer="119.90")

        mock_session.execute.side_effect = [
            _rows(
                [
                    (pp_pricey, pricey, product, store),
                    (pp_cheap, cheap, product, store),
                ]
            ),
            _rows([]),  # no alternatives resolved — best_alt stays None
            _rows([]),  # no floor either — timing stays unknown, like the verdict
        ]

        deals = client.get("/deals").json()
        assert [d["package_size"] for d in deals] == ["8-pack", "24-pack"]
        # ...even though the 8-pack is the more expensive one per unit.
        assert deals[0]["unit_price_sek"] > deals[1]["unit_price_sek"]
        assert all(d["best_alt_unit_price_sek"] is None for d in deals)
        # No alternative -> no verdict beyond "unknown", and no invented 0.0 margin.
        assert all(d["verdict"] == "unknown" for d in deals)
        assert all(d["savings_per_unit_sek"] is None for d in deals)

    def test_deals_flag_a_cheaper_store(self, client, mock_session):
        """The whole point of the comparison: an offer at one store is flagged against the
        product's cheapest OTHER link, so '20% rabatt' can be read as a real decision."""
        product = _product()
        willys, ica = _store(), _store(name="ICA", slug="ica")
        ps_offer = _ps(product, willys, package_size="8-pack", package_quantity="8")
        ps_cheap = _ps(product, ica, package_size="24-pack", package_quantity="24")
        pp_offer = _pp(ps_offer, price="59.90", offer="49.90")  # 6.24 kr/st on offer
        pp_cheap = _pp(ps_cheap, price="119.90")  # 5.00 kr/st ordinarie

        mock_session.execute.side_effect = [
            _rows([(pp_offer, ps_offer, product, willys)]),
            _rows([(ps_offer, willys, pp_offer), (ps_cheap, ica, pp_cheap)]),
            _rows([]),  # link-health query (v0.50.0): nothing broken
            _rows([(pp_offer, ps_offer, willys), (pp_cheap, ps_cheap, ica)]),
        ]

        deals = client.get("/deals").json()
        assert len(deals) == 1
        assert deals[0]["best_alt_store"] == "ICA"
        assert deals[0]["best_alt_unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)
        # The offer is PRICIER per unit than ICA's ordinarie — exactly what the user
        # must be able to see.
        assert deals[0]["unit_price_sek"] > deals[0]["best_alt_unit_price_sek"]
        assert deals[0]["verdict"] == "worse"

    def test_deals_window_is_seven_days(self, client, mock_session):
        """Most links are checked WEEKLY (Monday schedule) — a 24h window shows an empty
        deals page from Tuesday on. The service was fixed to 7 days; this pins the API
        route (Gotcha 4: the duplicated query drifted once already)."""
        mock_session.execute.return_value = _rows([])
        client.get("/deals")
        stmt = mock_session.execute.call_args_list[0].args[0]
        cutoff = next(v for v in stmt.compile().params.values() if isinstance(v, datetime))
        age_days = (datetime.now(UTC).replace(tzinfo=None) - cutoff).days
        assert age_days == 7 or age_days == 6  # 7 days minus test runtime rounding

    def test_swedish_offer_type_fallback(self, client, mock_session):
        """A missing offer_type must surface as Swedish user text, not 'unknown'."""
        product, store = _product(), _store()
        ps = _ps(product, store)
        pp = _pp(ps, price="139.90", offer="119.90")
        pp.offer_type = None
        mock_session.execute.side_effect = [
            _rows([(pp, ps, product, store)]),
            _rows([]),
            _rows([]),
        ]
        deals = client.get("/deals").json()
        assert deals[0]["offer_type"] == "erbjudande"


class TestManualPriceNotation:
    """POST /product-stores/{id}/prices — the manual half of discovery.

    ICA Björksätra advertises pre-order campaigns on Facebook (32-p toalettpapper at
    109 kr against a 162 kr shelf); the page the tracker fetches never shows it and the
    FB source cannot be scraped. The observation is still ordinary — same link, known
    day, a price that could really be paid — so it lands as a price point.
    """

    def _link(self, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_size="32-p", package_quantity="32")
        mock_session.execute.return_value = _scalar(ps)
        return ps

    def test_a_campaign_price_is_recorded_as_ordinarie_plus_offer(self, client, mock_session):
        """The shape every extractor produces (v0.25.2): price_sek is the ordinarie,
        offer_price_sek is what you pay."""
        ps = self._link(mock_session)

        r = client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 109, "regular_price_sek": 162, "note": "Förbokning via Facebook"},
        )

        assert r.status_code == 201
        added = mock_session.add.call_args.args[0]
        assert added.price_sek == Decimal("162.00")
        assert added.offer_price_sek == Decimal("109.00")
        assert added.offer_type == "manuellt pris"
        assert added.offer_details == "Förbokning via Facebook"
        assert added.raw_data["source"] == "manual"
        # 109/32 — the number the whole notation exists to put on record.
        assert r.json()["unit_price_sek"] == pytest.approx(3.41)

    def test_a_bare_price_is_no_campaign(self, client, mock_session):
        """No ordinarie given: a price that was charged, not an offer. Inventing one
        would put a fake deal in the buy list."""
        ps = self._link(mock_session)

        r = client.post(f"/product-stores/{ps.id}/prices", json={"price_sek": 109})

        assert r.status_code == 201
        added = mock_session.add.call_args.args[0]
        assert added.price_sek == Decimal("109.00")
        assert added.offer_price_sek is None
        assert added.offer_type is None

    def test_an_inverted_pair_is_refused(self, client, mock_session):
        """The v0.32.1 invariant, at the one place a human can type it: an offer is what
        you PAY. Inverted, it would re-price the link at the HIGHER number everywhere,
        because effective_price = coalesce(offer, price)."""
        ps = self._link(mock_session)

        r = client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 162, "regular_price_sek": 109},
        )

        assert r.status_code == 400
        assert "Ordinarie" in r.json()["detail"]
        mock_session.add.assert_not_called()

    def test_the_notation_is_not_a_check(self, client, mock_session):
        """last_checked_at must keep meaning 'we looked at the store's page'. Nothing
        here looked at anything — the same rule that stopped a blocked check from
        stamping it (v0.29.2)."""
        ps = self._link(mock_session)
        ps.last_checked_at = None

        client.post(f"/product-stores/{ps.id}/prices", json={"price_sek": 109})

        assert ps.last_checked_at is None

    def test_a_date_is_a_swedish_civil_day(self, client, mock_session):
        """Midday Swedish, converted to the app's naive-UTC storage convention — midday
        so a date typed today lands AFTER that morning's scheduled check and therefore
        becomes the link's latest point."""
        ps = self._link(mock_session)

        client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 109, "observed_on": "2026-08-04"},
        )

        added = mock_session.add.call_args.args[0]
        # 12:00 in CEST (UTC+2) = 10:00 UTC, stored naive.
        assert added.checked_at == datetime(2026, 8, 4, 10, 0, 0)

    def test_an_unparseable_date_is_refused_in_swedish(self, client, mock_session):
        ps = self._link(mock_session)

        r = client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 109, "observed_on": "4 augusti"},
        )

        assert r.status_code == 400
        mock_session.add.assert_not_called()

    def test_a_future_date_is_refused(self, client, mock_session):
        """A point dated in the future is permanently the link's LATEST: it outranks
        every real check, and its checked_at >= now-7d forever, so it sits in the buy
        list and the Monday email every week — and no path deletes a single point."""
        ps = self._link(mock_session)

        r = client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 109, "observed_on": "2031-01-01"},
        )

        assert r.status_code == 400
        assert "framtiden" in r.json()["detail"]
        mock_session.add.assert_not_called()

    def test_a_note_without_an_offer_is_no_offer_details(self, client, mock_session):
        """offer_details beside a plain price would describe an offer that does not
        exist — the note survives in raw_data, where the source marker lives."""
        ps = self._link(mock_session)

        client.post(
            f"/product-stores/{ps.id}/prices",
            json={"price_sek": 109, "note": "sett i butiken"},
        )

        added = mock_session.add.call_args.args[0]
        assert added.offer_details is None
        assert added.raw_data["note"] == "sett i butiken"

    def test_a_missing_link_is_404(self, client, mock_session):
        mock_session.execute.return_value = _scalar(None)

        r = client.post(f"/product-stores/{uuid.uuid4()}/prices", json={"price_sek": 109})

        assert r.status_code == 404


class TestWatchesEndpoint:
    def test_watches_carry_the_current_lowest_price(self, client, mock_session):
        """A watch row must answer 'how close is it?' — the current cheapest effective
        price (offer wins) across the product's links, with its store."""
        from domain.models import PriceWatch

        product, store = _product(), _store()
        watch = PriceWatch(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(TENANT),
            product_id=product.id,
            email_address="magnus@example.com",
            alert_on_any_offer=True,
            created_at=datetime(2026, 7, 1),
        )
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90", offer="119.90")

        mock_session.execute.side_effect = [
            _rows([(watch, product)]),
            _rows([(ps, store, pp)]),
            _rows([(pp, ps, store)]),  # the observed span walk
        ]

        watches = client.get("/watches").json()
        assert len(watches) == 1
        assert watches[0]["current_lowest_price_sek"] == pytest.approx(119.90)
        # _computed_unit_price rounds to 2 decimals: 119.90/24 = 4.9958… → 5.00
        assert watches[0]["current_lowest_unit_price_sek"] == pytest.approx(5.0)
        assert watches[0]["current_lowest_store"] == "Willys"
        assert watches[0]["unit"] == "st"

    def test_one_observation_is_no_span(self, client, mock_session):
        """The floor rule the buy list already follows, carried here unchanged: with a
        single observation the product's own point IS the low and the high, and a watch
        page that drew a span from it would place every target at an end of a range that
        was never seen. Null instead — the client draws nothing."""
        from domain.models import PriceWatch

        product, store = _product(), _store()
        watch = PriceWatch(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(TENANT),
            product_id=product.id,
            email_address="magnus@example.com",
            unit_price_target_sek=Decimal("4.00"),
            created_at=datetime(2026, 7, 1),
        )
        ps = _ps(product, store, package_quantity="24")
        pp = _pp(ps, price="139.90")

        mock_session.execute.side_effect = [
            _rows([(watch, product)]),
            _rows([(ps, store, pp)]),
            _rows([(pp, ps, store)]),
        ]

        row = client.get("/watches").json()[0]
        assert row["lowest_unit_price_sek"] is None
        assert row["highest_unit_price_sek"] is None

    def test_the_span_is_the_buy_lists_own_floor_window(self, client, mock_session):
        """Two observations make a span, and it is the SAME span domain/deals.py draws its
        floor and its bar from — one definition, so the two pages can never disagree about
        what a product has cost. A unit target under the low is what the page calls
        'larmar inte som det är satt'."""
        from domain.models import PriceWatch

        product, store = _product(), _store()
        watch = PriceWatch(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(TENANT),
            product_id=product.id,
            email_address="magnus@example.com",
            unit_price_target_sek=Decimal("4.00"),
            created_at=datetime(2026, 7, 1),
        )
        ps = _ps(product, store, package_quantity="24")
        high = _pp(ps, price="139.90")  # 5.83 kr/st
        low = _pp(ps, price="119.90")  # 5.00 kr/st

        mock_session.execute.side_effect = [
            _rows([(watch, product)]),
            _rows([(ps, store, low)]),
            _rows([(high, ps, store), (low, ps, store)]),
        ]

        row = client.get("/watches").json()[0]
        assert row["lowest_unit_price_sek"] == pytest.approx(5.0)
        assert row["highest_unit_price_sek"] == pytest.approx(5.83)
        assert row["lowest_store"] == "Willys"
        assert row["lowest_seen_at"] is not None
        assert row["span_window_days"] == 84


class TestUpdateWatch:
    """PUT /watches/{id} — the endpoint behind the edit dialog.

    The load-bearing case is CLEARING. The old handler used `if value is not None`, so once a
    watch had a target price no request could ever remove it: editing was a one-way door and
    an emptied field silently kept its old value.
    """

    def _watch(self):
        from domain.models import PriceWatch

        return PriceWatch(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(TENANT),
            product_id=uuid.uuid4(),
            email_address="magnus@example.com",
            target_price_sek=Decimal("99.00"),
            unit_price_target_sek=Decimal("4.50"),
            price_drop_threshold_percent=20,
            alert_on_any_offer=True,
        )

    def test_explicit_null_clears_a_target(self, client, mock_session):
        watch = self._watch()
        mock_session.execute.return_value = _scalar(watch)

        r = client.put(
            f"/watches/{watch.id}",
            json={"target_price_sek": None, "unit_price_target_sek": None},
        )

        assert r.status_code == 200
        assert watch.target_price_sek is None
        assert watch.unit_price_target_sek is None

    def test_omitted_field_is_left_alone(self, client, mock_session):
        """Partial update must survive: a body that never mentions a field cannot change it."""
        watch = self._watch()
        mock_session.execute.return_value = _scalar(watch)

        r = client.put(f"/watches/{watch.id}", json={"unit_price_target_sek": 3.25})

        assert r.status_code == 200
        assert watch.unit_price_target_sek == Decimal("3.25")
        assert watch.target_price_sek == Decimal("99.00")  # untouched
        assert watch.price_drop_threshold_percent == 20  # untouched

    def test_thresholds_clear_too(self, client, mock_session):
        watch = self._watch()
        mock_session.execute.return_value = _scalar(watch)

        r = client.put(f"/watches/{watch.id}", json={"price_drop_threshold_percent": None})

        assert r.status_code == 200
        assert watch.price_drop_threshold_percent is None

    def test_email_cannot_be_cleared(self, client, mock_session):
        """email_address is NOT NULL — a null there is a malformed request, not 'clear it'."""
        watch = self._watch()
        mock_session.execute.return_value = _scalar(watch)

        r = client.put(f"/watches/{watch.id}", json={"email_address": None})

        assert r.status_code == 400
        assert watch.email_address == "magnus@example.com"

    def test_invalid_email_is_rejected(self, client, mock_session):
        watch = self._watch()
        mock_session.execute.return_value = _scalar(watch)

        r = client.put(f"/watches/{watch.id}", json={"email_address": "inte-en-adress"})

        assert r.status_code == 400
        assert watch.email_address == "magnus@example.com"

    def test_missing_watch_is_404(self, client, mock_session):
        mock_session.execute.return_value = _scalar(None)

        r = client.put(f"/watches/{uuid.uuid4()}", json={"target_price_sek": 10})

        assert r.status_code == 404


class TestManualCheckAppliesTheScrapeRule:
    """POST /check/{id} routes through domain.service.perform_price_check (D-07, AC#4).

    The endpoint used to duplicate the fetch/parse/record flow inline; it now delegates
    to the single shared flow, and these tests pin the D-07 rule still reaching it:
    without the scrape write path, the operator clicks "Check now", a price appears,
    and the link's quantity stays empty forever.
    """

    def _run_check(self, client, mock_session, mock_service, link, product, store, extraction):
        row = MagicMock()
        row.one_or_none.return_value = (link, store, product)
        mock_session.execute.return_value = row

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page", "html": "<html>"})
        parser = MagicMock()
        parser.extract_price = AsyncMock(return_value=extraction)

        with (
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.PriceParser", return_value=parser),
        ):
            return client.post(f"/check/{link.id}")

    def test_check_autofills_an_empty_link_quantity(self, client, mock_session, mock_service):
        product, store = _product(unit="st"), _store()
        link = _ps(product, store, package_size=None, package_quantity=None)
        extraction = _extraction(package_amount="24", package_unit="st")

        r = self._run_check(client, mock_session, mock_service, link, product, store, extraction)

        assert r.status_code == 200
        body = r.json()
        # The STORED value was filled — not merely reported back.
        assert link.package_quantity == Decimal("24.00")
        assert link.scraped_package_quantity == Decimal("24.00")
        assert body["package_quantity"] == pytest.approx(24.0)
        assert body["quantity_mismatch"] is None
        assert body["unit_price_sek"] == pytest.approx(5.83)  # computed from the new quantity
        assert body["store_unit_price_sek"] == pytest.approx(5.83)

    def test_check_flags_a_conflict_and_never_overwrites(self, client, mock_session, mock_service):
        """The typed value is intent; the page is evidence. Evidence does not rewrite intent."""
        product, store = _product(unit="st"), _store()
        link = _ps(product, store, package_quantity="24")
        extraction = _extraction(package_amount="12", package_unit="st", store_unit="11.66")

        r = self._run_check(client, mock_session, mock_service, link, product, store, extraction)

        assert r.status_code == 200
        body = r.json()
        # The stored quantity is UNTOUCHED — assert on the object, not just the response.
        assert link.package_quantity == Decimal("24")
        assert link.scraped_package_quantity == Decimal("12.00")
        assert body["quantity_mismatch"], "the page/operator conflict was not surfaced"
        assert "12" in body["quantity_mismatch"] and "24" in body["quantity_mismatch"]
        assert body["package_quantity"] == pytest.approx(24.0)

    def test_check_success_keeps_the_exact_wire_keys(self, client, mock_session, mock_service):
        """Behavior parity: the consolidation must not change the success JSON's key set."""
        product, store = _product(unit="st"), _store()
        link = _ps(product, store, package_quantity="24")
        extraction = _extraction()

        r = self._run_check(client, mock_session, mock_service, link, product, store, extraction)

        assert r.status_code == 200
        assert set(r.json().keys()) == {
            "message",
            "price_sek",
            "unit_price_sek",
            "store_unit_price_sek",
            "package_quantity",
            "quantity_mismatch",
            "offer_price_sek",
            "offer_type",
            "in_stock",
            "confidence",
        }

    def test_check_no_price_keeps_the_exact_wire_keys(self, client, mock_session, mock_service):
        """The no-price shape keeps message/confidence/price_sek/offer_price_sek."""
        product, store = _product(unit="st"), _store()
        link = _ps(product, store)
        extraction = PriceExtractionResult(
            price_sek=None,
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.3,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={"source": "discarded_low_confidence"},
        )

        r = self._run_check(client, mock_session, mock_service, link, product, store, extraction)

        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"message", "confidence", "price_sek", "offer_price_sek"}
        assert body["price_sek"] is None
        assert body["confidence"] == pytest.approx(0.3)

    def test_check_fetch_failure_returns_502_with_error(self, client, mock_session, mock_service):
        product, store = _product(), _store()
        link = _ps(product, store)

        row = MagicMock()
        row.one_or_none.return_value = (link, store, product)
        mock_session.execute.return_value = row

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value={"ok": False, "error": "connection refused"})

        with patch("api.admin.get_fetcher", return_value=fetcher):
            r = client.post(f"/check/{link.id}")

        assert r.status_code == 502
        assert "connection refused" in r.json()["detail"]

    def test_check_missing_link_returns_404(self, client, mock_session, mock_service):
        row = MagicMock()
        row.one_or_none.return_value = None
        mock_session.execute.return_value = row

        r = client.post(f"/check/{LINK_ID}")
        assert r.status_code == 404

    def test_check_invalid_uuid_returns_400(self, client, mock_session, mock_service):
        r = client.post("/check/not-a-uuid")
        assert r.status_code == 400


class TestSchedulerEndpoints:
    def test_scheduler_status(self, client):
        # Lifespan doesn't run under a plain (non-context-manager) TestClient,
        # so app.state.scheduler is never set — set it explicitly here.
        client.app.state.scheduler = None
        r = client.get("/scheduler/status")
        assert r.status_code == 200
        data = r.json()
        assert "running" in data

    def test_scheduler_status_wire_shape_matches_what_the_ui_reads(self, client, mock_session):
        """Pin the exact keys renderSchedulerFooter branches on. The frontend has a FIXED
        bug riding on this shape (parseUtc on a naive-UTC paused_until — a bare timestamp
        parsed as LOCAL read the pause 2h in the past), and blocked_stores' three fields
        render straight into the status tooltip — a renamed key fails silently there.
        """
        from datetime import datetime as _dt
        from datetime import timedelta

        from domain.scheduler import PriceCheckScheduler
        from infra.store_block import StoreBlockRegistry

        registry = StoreBlockRegistry()
        registry.record_block("store-1", store_name="ICA")
        scheduler = PriceCheckScheduler(
            session_factory=MagicMock(),
            fetcher=MagicMock(),
            block_registry=registry,
        )
        scheduler.pause_for(timedelta(minutes=30))
        client.app.state.scheduler = scheduler

        last = _dt(2026, 8, 3, 7, 30, 0)
        nxt = _dt(2026, 8, 10, 6, 15, 0)
        bounds = MagicMock()
        bounds.first.return_value = (last, nxt)
        mock_session.execute.return_value = bounds

        data = client.get("/scheduler/status").json()

        assert data["running"] is False
        # Naive-UTC isoformat — NO offset suffix: parseUtc exists because of exactly that.
        assert "+" not in data["paused_until"] and not data["paused_until"].endswith("Z")
        _dt.fromisoformat(data["paused_until"])
        blocked = data["blocked_stores"]
        assert blocked and set(blocked[0]) == {"store", "blocked_until", "consecutive_blocks"}
        assert blocked[0]["store"] == "ICA"
        assert blocked[0]["consecutive_blocks"] == 1
        _dt.fromisoformat(blocked[0]["blocked_until"])
        assert data["last_check_at"] == last.isoformat()
        assert data["next_check_at"] == nxt.isoformat()
        assert isinstance(data["stats"], dict)


class TestValidation:
    def test_create_product_rejects_foreign_tenant(self, client):
        r = client.post(
            "/products",
            json={"tenant_id": "11111111-2222-3333-4444-555555555555", "name": "X"},
        )
        assert r.status_code == 403

    def test_create_product_rejects_malformed_tenant(self, client):
        r = client.post("/products", json={"tenant_id": "not-a-uuid", "name": "X"})
        assert r.status_code == 400

    def test_create_watch_rejects_invalid_email(self, client):
        r = client.post(
            "/watches?tenant_id=f21b6620-c793-46e3-a354-dfcd9956b4a2",
            json={"product_id": "p1", "email_address": "not-an-email"},
        )
        assert r.status_code == 400

    def test_create_watch_rejects_foreign_tenant(self, client):
        r = client.post(
            "/watches?tenant_id=11111111-2222-3333-4444-555555555555",
            json={"product_id": "p1", "email_address": "a@b.se"},
        )
        assert r.status_code == 403


class TestLogsEndpoint:
    """GET /logs surfaces the in-memory ring buffer for the portal's Loggar page."""

    def test_returns_recent_records_newest_first(self, client):
        import logging

        from infra.logbuffer import get_log_buffer

        get_log_buffer().clear()
        logging.getLogger("domain.parser").warning("meta extraction failed for X")
        logging.getLogger("domain.service").info("Price extracted via JSON-LD")

        r = client.get("/logs")
        assert r.status_code == 200
        body = r.json()
        messages = [rec["message"] for rec in body["logs"]]
        assert body["count"] == len(body["logs"])
        assert messages[0] == "Price extracted via JSON-LD"  # newest first
        assert any("meta extraction failed" in m for m in messages)

    def test_level_filter_excludes_below_threshold(self, client):
        import logging

        from infra.logbuffer import get_log_buffer

        get_log_buffer().clear()
        logging.getLogger("domain.parser").info("an info line")
        logging.getLogger("domain.parser").error("a real error")

        messages = [rec["message"] for rec in client.get("/logs?level=ERROR").json()["logs"]]
        assert messages == ["a real error"]

    def test_limit_is_clamped(self, client):
        r = client.get("/logs?limit=999999")
        assert r.status_code == 200  # clamped server-side, never rejected


class TestInteractiveFetchesRespectTheCircuitBreaker:
    """A bot wall must silence EVERY caller against that store, not just the scheduler.

    The breaker lived inside PriceCheckScheduler until v0.28.0, so a blocked store only
    stopped background checks. "Kolla nu" and the quick-add preview kept firing at a WAF that
    was actively challenging us — which is exactly the traffic that keeps an IP flag alive,
    and exactly what a human does when the page says the fetch failed.
    """

    def _registry(self):
        from infra.store_block import StoreBlockRegistry

        # A private instance per test: get_block_registry() is a process-wide singleton and
        # leaked block state would make these tests order-dependent.
        return StoreBlockRegistry()

    def _link_row(self, mock_session):
        product, store = _product(unit="st"), _store()
        link = _ps(product, store, package_quantity="24")
        row = MagicMock()
        row.one_or_none.return_value = (link, store, product)
        mock_session.execute.return_value = row
        return link, product, store

    def test_manual_check_is_refused_while_the_store_is_blocked(self, client, mock_session):
        link, _product_obj, store = self._link_row(mock_session)
        registry = self._registry()
        registry.record_block(store.id, store_name=store.name, source="scheduler")

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock()

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_fetcher", return_value=fetcher),
        ):
            r = client.post(f"/check/{link.id}")

        assert r.status_code == 503
        # No request left the process — that is the whole point.
        fetcher.fetch.assert_not_awaited()
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) > 0
        assert store.name in r.json()["detail"]

    def test_a_manual_check_that_hits_a_wall_trips_the_shared_breaker(self, client, mock_session):
        """The scheduler must learn about a block the operator discovered."""
        link, _product_obj, store = self._link_row(mock_session)
        registry = self._registry()

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={
                "ok": False,
                "text": "",
                "html": "",
                "error": "blocked (HTTP 202)",
                "blocked": True,
            }
        )

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_fetcher", return_value=fetcher),
        ):
            r = client.post(f"/check/{link.id}")

        # A block answers 503 (come back later), not 502 (the page is broken).
        assert r.status_code == 503
        assert registry.blocked_until(store.id) is not None

    def test_a_successful_manual_check_clears_the_breaker(self, client, mock_session):
        link, _product_obj, store = self._link_row(mock_session)
        registry = self._registry()
        registry.record_block(store.id, store_name=store.name, source="scheduler")
        # Pretend the cooldown lapsed so the guard lets this probe through.
        registry._until.pop(store.id)

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page", "html": "<html>"})
        parser = MagicMock()
        parser.extract_price = AsyncMock(return_value=_extraction())

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.PriceParser", return_value=parser),
        ):
            r = client.post(f"/check/{link.id}")

        assert r.status_code == 200
        # The strike count is reset, so a later block starts over at the base cooldown
        # instead of doubling off a stale count.
        assert registry._strikes.get(store.id, 0) == 0

    def test_quick_add_preview_is_refused_while_the_store_is_blocked(self, client, mock_session):
        store = _store(name="ICA", slug="ica")
        stores_result = MagicMock()
        stores_result.scalars.return_value.all.return_value = [store]
        # The same mock answers the duplicate-URL probe; None = "not tracked yet", so the
        # request reaches the guard instead of short-circuiting on already_tracked.
        stores_result.first.return_value = None
        mock_session.execute = AsyncMock(return_value=stores_result)

        registry = self._registry()
        registry.record_block(store.id, store_name=store.name, source="scheduler")

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock()

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.match_store_by_url", return_value=store),
        ):
            r = client.post(
                "/quick-add/preview",
                json={"url": "https://www.ica.se/handla/produkt/nagot-123"},
            )

        assert r.status_code == 503
        fetcher.fetch.assert_not_awaited()

    def test_preview_api_tier_wall_trips_the_breaker_and_skips_the_page_fetch(
        self, client, mock_session
    ):
        """A wall at the store-API tier used to degrade to None, which fell through to a
        SECOND request (the page fetch) at the very host that just walled the first — and
        only that one tripped the breaker. Now the wall itself trips it, answers 503, and
        the page fetch never fires."""
        from domain.result import StoreBlockedError

        store = _store(name="Willys", slug="willys")
        stores_result = MagicMock()
        stores_result.scalars.return_value.all.return_value = [store]
        stores_result.first.return_value = None  # not already tracked
        mock_session.execute = AsyncMock(return_value=stores_result)

        registry = self._registry()
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock()
        api_extractor = MagicMock()
        api_extractor.extract_metadata = AsyncMock(
            side_effect=StoreBlockedError("Willys API bot wall: blocked (HTTP 403)")
        )

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.get_api_extractor", return_value=api_extractor),
            patch("api.admin.match_store_by_url", return_value=store),
        ):
            r = client.post(
                "/quick-add/preview",
                json={"url": "https://www.willys.se/produkt/Mjolk-100014716_ST"},
            )

        assert r.status_code == 503
        assert "Retry-After" in r.headers
        # The breaker holds the whole store now — scheduler AND the next click stand down.
        assert registry.blocked_until(store.id) is not None
        fetcher.fetch.assert_not_awaited()


class TestBladAnalyzeEndpoint:
    """The HTTP wiring over domain/blad.py — untested until v0.51.0 despite being an
    admin POST that spends a real ledger slot against willys.se, trips the SHARED
    circuit breaker, and carries an inline copy of the validator judgement
    (crosscheck_warning) with hand-tuned thresholds. The pure functions are pinned in
    test_blad.py; these pin the status codes, the breaker interplay and the
    check_attempts accounting."""

    URL = "https://www.willys.se/erbjudanden/offline-Lask-15-pack-2500310468"

    def _offer_data(self, **overrides) -> dict:
        data = {
            "priceNoUnit": "99,90",
            "priceUnit": "kr/st",
            "displayVolume": "15p/33cl",
            "online": False,
            "manufacturer": "COCA-COLA",
            "potentialPromotions": [
                {
                    "price": 59.8,
                    "comparePrice": "12:08 kr/l +pant",
                    "code": "2500310468",
                    "weightVolume": "15p/33cl",
                    "qualifyingCount": 1,
                    "cartLabel": "59,80/st  +pant",
                    "rewardLabel": "59,80/st  +pant",
                    "brands": ["COCA-COLA"],
                    "name": "Läsk 15-pack",
                }
            ],
        }
        data.update(overrides)
        return data

    def _registry(self):
        from infra.store_block import StoreBlockRegistry

        return StoreBlockRegistry()

    def _willys_row(self, mock_session):
        store = _store(name="Willys", slug="willys")
        row = MagicMock()
        row.scalar_one_or_none.return_value = store
        candidates_row = MagicMock()
        candidates_row.all.return_value = []
        mock_session.execute.side_effect = [row, candidates_row]
        return store

    def test_url_without_a_code_is_400(self, client, mock_session):
        r = client.post("/blad/analyze", json={"url": "https://www.willys.se/erbjudanden"})
        assert r.status_code == 400
        mock_session.execute.assert_not_called()

    def test_missing_willys_store_is_400(self, client, mock_session):
        row = MagicMock()
        row.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = row
        r = client.post("/blad/analyze", json={"url": self.URL})
        assert r.status_code == 400

    def test_refused_while_the_breaker_is_open(self, client, mock_session):
        """No request may leave the process against a store that is walling us."""
        store = self._willys_row(mock_session)
        registry = self._registry()
        registry.record_block(store.id, store_name=store.name, source="scheduler")
        fetch = AsyncMock()

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.fetch_offline_offer", fetch),
        ):
            r = client.post("/blad/analyze", json={"url": self.URL})

        assert r.status_code == 503
        assert "Retry-After" in r.headers
        fetch.assert_not_awaited()

    def test_a_wall_trips_the_shared_breaker_and_records_the_attempt(self, client, mock_session):
        """StoreBlockedError from the offer API = the breaker learns it, check_attempts
        records it (real load, real WAF exposure — the quick-add-preview rule), and the
        caller gets a machine-readable refusal."""
        from domain.result import StoreBlockedError

        store = self._willys_row(mock_session)
        registry = self._registry()
        check_log = MagicMock()
        check_log.record = AsyncMock()

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_check_log", return_value=check_log),
            patch(
                "api.admin.fetch_offline_offer",
                AsyncMock(side_effect=StoreBlockedError("HTTP 403")),
            ),
        ):
            r = client.post("/blad/analyze", json={"url": self.URL})

        assert r.status_code == 503  # the freshly-tripped breaker answers first
        assert registry.blocked_until(store.id) is not None
        assert check_log.record.await_args.kwargs["outcome"] == "blocked"
        assert check_log.record.await_args.kwargs["source"] == "blad-analyze"

    def test_missing_offer_is_404(self, client, mock_session):
        self._willys_row(mock_session)
        registry = self._registry()
        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.fetch_offline_offer", AsyncMock(return_value=None)),
        ):
            r = client.post("/blad/analyze", json={"url": self.URL})
        assert r.status_code == 404

    def test_success_returns_the_stores_numbers_and_records_ok(self, client, mock_session):
        store = self._willys_row(mock_session)
        registry = self._registry()
        check_log = MagicMock()
        check_log.record = AsyncMock()

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_check_log", return_value=check_log),
            patch("api.admin.fetch_offline_offer", AsyncMock(return_value=self._offer_data())),
        ):
            r = client.post("/blad/analyze", json={"url": self.URL})

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == "2500310468"
        assert body["offer_price_sek"] == pytest.approx(59.80)
        assert body["ordinarie_price_sek"] == pytest.approx(99.90)
        assert body["candidates"] == []
        assert check_log.record.await_args.kwargs["outcome"] == "ok"
        assert registry.blocked_until(store.id) is None

    def test_crosscheck_warns_when_the_printed_jamforpris_disagrees(self, client, mock_session):
        """The inline validator judgement: printed jämförpris vs offer/package, only when
        the units agree. 59,80 kr for 15 st = 3,99 kr/st against a printed 12,08 kr/st
        is far past both thresholds (0,10 kr AND 1,5 %)."""
        self._willys_row(mock_session)
        registry = self._registry()
        data = self._offer_data(displayVolume="15st")
        data["potentialPromotions"][0]["comparePrice"] = "12:08 kr/st"
        data["potentialPromotions"][0]["weightVolume"] = "15st"

        with (
            patch("api.admin.get_block_registry", return_value=registry),
            patch("api.admin.get_check_log", return_value=MagicMock(record=AsyncMock())),
            patch("api.admin.fetch_offline_offer", AsyncMock(return_value=data)),
        ):
            body = client.post("/blad/analyze", json={"url": self.URL}).json()

        # Preconditions first, so a parser change cannot silently void the assertion.
        assert body["unit_price_sek"] == pytest.approx(12.08)
        assert body["crosscheck_warning"], body


class TestBladCandidatesCarryNotableLinks:
    """The bridge's wire contract: a candidate row must say WHERE a manual note may
    honestly land, or the portal's "Notera på …" button has nothing to target. Pinned at
    the HTTP layer because the button reads this payload, not the domain object."""

    def test_notable_links_ride_the_candidate(self, client, mock_session):
        product = _product(name="Läsk Cola Zero 33cl", unit="liter")
        product.brand = "Coca-Cola"
        willys = _store(name="Willys", slug="willys")
        link = _ps(product, willys, package_size="20-pack", package_quantity="6.6")
        point = _pp(link, price="137.45")

        store_row = MagicMock()
        store_row.scalar_one_or_none.return_value = willys
        candidates_row = MagicMock()
        candidates_row.all.return_value = [(product, link, willys, point)]
        mock_session.execute.side_effect = [store_row, candidates_row]

        data = {
            "priceNoUnit": "99,90",
            "priceUnit": "kr/st",
            "displayVolume": "15p/33cl",
            "online": False,
            "manufacturer": "COCA-COLA",
            "potentialPromotions": [
                {
                    "price": 59.8,
                    "comparePrice": "12:08 kr/l",
                    "code": "2500310468",
                    "weightVolume": "15p/33cl",
                    "qualifyingCount": 1,
                    "cartLabel": "59,80/st",
                    "rewardLabel": "59,80/st",
                    "brands": ["COCA-COLA"],
                    "name": "Läsk 15-pack",
                }
            ],
        }

        with (
            patch(
                "api.admin.get_block_registry",
                return_value=MagicMock(
                    blocked_until=MagicMock(return_value=None),
                    record_success=MagicMock(),
                ),
            ),
            patch("api.admin.get_check_log", return_value=MagicMock(record=AsyncMock())),
            patch("api.admin.fetch_offline_offer", AsyncMock(return_value=data)),
        ):
            body = client.post(
                "/blad/analyze",
                json={"url": "https://www.willys.se/erbjudanden/offline-Lask-2500310468"},
            ).json()

        assert body["candidates"], body
        links = body["candidates"][0]["notable_links"]
        assert len(links) == 1
        assert links[0]["product_store_id"] == str(link.id)
        assert links[0]["store_name"] == "Willys"
        assert links[0]["package_size"] == "20-pack"
        assert links[0]["price_sek"] == pytest.approx(137.45)
