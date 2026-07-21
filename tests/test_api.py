"""Tests for FastAPI admin endpoints and auth."""

import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import require_auth
from domain.models import PricePoint, Product, ProductStore, Store
from domain.result import PriceExtractionResult

TENANT = "f21b6620-c793-46e3-a354-dfcd9956b4a2"
# Any well-formed UUID; the session is mocked, so nothing resolves it.
LINK_ID = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
CHECKED_AT = datetime(2026, 7, 14, 6, 0)


@pytest.fixture
def mock_session():
    """Mock async DB session."""
    return AsyncMock()


@pytest.fixture
def client(mock_session):
    """FastAPI TestClient with mocked auth and DB."""
    app = create_app()

    async def override_auth():
        return "test@example.com"

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[require_auth] = override_auth
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
        check_frequency_hours=72,
        check_weekday=None,
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


class TestAuth:
    def test_admin_rejects_missing_header(self):
        app = create_app()
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 403

    def test_admin_rejects_wrong_email(self):
        app = create_app()
        client = TestClient(app)
        r = client.get("/admin/", headers={"X-Auth-Request-Email": "attacker@evil.com"})
        assert r.status_code == 403


class TestAdminDashboard:
    def test_dashboard_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Price Tracker" in r.text

    def test_no_admin_wording_reaches_the_user(self, client):
        """The 'Admin' naming was a holdover from the source platform — the app is just
        'Price Tracker' now. CSS comments may say what they like; visible text may not."""
        r = client.get("/")
        assert "<title>Price Tracker</title>" in r.text
        assert "Price Tracker Admin" not in r.text
        assert ">Admin<" not in r.text

    def test_sidebar_footer_shows_the_version(self, client):
        """The footer version comes from the same source of truth as the release tag
        (pyproject.toml / installed metadata), so what you see is what prod runs."""
        r = client.get("/")
        assert "Price Tracker v" in r.text

    def test_sidebar_has_one_nav_item_per_page(self, client):
        """The three sections are hash-routed pages picked from the left menu — a long
        product list must never push the deals out of sight."""
        r = client.get("/")
        for page in ("produkter", "erbjudanden", "bevakningar"):
            assert f'data-page="{page}"' in r.text, f"nav/page missing for {page}"
            assert f'href="#/{page}"' in r.text
        # The page sections themselves exist for the router to toggle.
        assert r.text.count('class="app-page"') == 3

    def test_check_day_is_a_named_weekday_choice(self, client):
        """Both link forms (manual + quick-add) offer the check day as a Swedish weekday
        select, not a bare 0-6 number — a weekly check on the chain's offer day is the
        lightest possible schedule against the stores' sites, so it must be easy to pick."""
        r = client.get("/")
        assert 'id="qa-weekday"' in r.text
        assert 'name="check_weekday"' in r.text
        # Three forms carry the select: quick-add, manual link, and the link-edit dialog.
        assert r.text.count("Måndag — nya veckoerbjudanden") == 3
        # No raw number input for the weekday anywhere.
        assert 'type="number" name="check_weekday"' not in r.text

    def test_product_edit_dialog_exists_with_locked_unit(self, client):
        """Identity fields are editable from the product row; the unit is displayed but
        disabled — it is the scale of every link amount and the whole kr/unit history,
        so changing it means delete + recreate, never an edit."""
        r = client.get("/")
        assert 'id="modal-edit-product"' in r.text
        assert 'data-action="edit"' in r.text
        assert 'id="edit-product-unit" class="form-input" disabled' in r.text

    def test_link_dialog_edits_cadence(self, client):
        """Existing links' check schedule is editable (PUT /frequency finally has a UI) —
        without this, moving a link to Monday required delete + recreate."""
        r = client.get("/")
        assert 'id="edit-weekday"' in r.text
        assert 'id="edit-frequency"' in r.text
        assert "/frequency'" in r.text  # the JS actually calls the endpoint

    def test_deals_is_the_start_page(self, client):
        """Erbjudanden first in the menu and the default page: the freshest, most
        actionable view opens on load; the long product list is one click away."""
        r = client.get("/")
        # Menu order: Erbjudanden above Produkter.
        assert r.text.index('href="#/erbjudanden"') < r.text.index('href="#/produkter"')
        # The server-rendered breadcrumb matches the client router's fallback.
        assert 'id="breadcrumb-current">Aktuella erbjudanden' in r.text
        assert ": 'erbjudanden'" in r.text  # currentPage() fallback


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
        assert product.category == "Mejeri"

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
    def test_update_frequency_by_link_id(self, client, mock_session):
        ps = MagicMock()
        _link_row(mock_session, ps)

        r = client.put(
            f"/product-stores/{LINK_ID}/frequency",
            json={"check_frequency_hours": 168, "check_weekday": 2},
        )
        assert r.status_code == 200, r.text
        assert r.json()["message"] == "Frequency updated"
        assert ps.check_frequency_hours == 168
        assert ps.check_weekday == 2
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
            json={"check_frequency_hours": 168, "check_weekday": 9},
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
        ]

        r = client.get("/products")
        link = r.json()[0]["stores"][0]

        assert link["unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)  # ~5.00
        assert link["unit_price_sek"] != pytest.approx(139.90 / 24, rel=1e-3)  # not the regular

    def test_get_product_carries_the_computed_price_and_both_flags(self, client, mock_session):
        product, store = _product(), _store()
        ps = _ps(product, store, package_quantity="8", scraped_package_quantity="8")
        pp = _pp(ps, price="59.90", store_unit="8.10")
        mock_session.execute.side_effect = [_scalar(product), _rows([(ps, store)]), _scalar(pp)]

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
        mock_session.execute.side_effect = [_rows([(pp, store, ps)])]

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

        mock_session.execute.return_value = _rows(
            [
                (pp24, product, store, ps24),
                (pp8, product, store, ps8),
            ]
        )

        r = client.get("/deals")
        assert r.status_code == 200
        deals = r.json()

        assert len(deals) == 2, "the two pack sizes at one store collapsed into one deal row"
        assert {d["package_size"] for d in deals} == {"24-pack", "8-pack"}
        assert all(d["store_name"] == "Willys" for d in deals)

        by_pack = {d["package_size"]: d for d in deals}
        assert by_pack["24-pack"]["unit_price_sek"] == pytest.approx(119.90 / 24, rel=1e-3)
        assert by_pack["8-pack"]["unit_price_sek"] == pytest.approx(49.90 / 8, rel=1e-3)

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

        mock_session.execute.return_value = _rows(
            [
                (pp_pricey, product, store, pricey),
                (pp_cheap, product, store, cheap),
            ]
        )

        deals = client.get("/deals").json()
        assert [d["package_size"] for d in deals] == ["8-pack", "24-pack"]
        # ...even though the 8-pack is the more expensive one per unit.
        assert deals[0]["unit_price_sek"] > deals[1]["unit_price_sek"]


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
