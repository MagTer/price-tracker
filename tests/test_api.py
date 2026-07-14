"""Tests for FastAPI admin endpoints and auth."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import require_auth
from infra.db import async_session_factory

TENANT = "f21b6620-c793-46e3-a354-dfcd9956b4a2"
# Any well-formed UUID; the session is mocked, so nothing resolves it.
LINK_ID = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


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
        assert "positive" in r.json()["detail"]
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
                Exception('duplicate key value violates unique constraint "uq_product_stores_store_url"'),
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
        assert "already tracked" in detail
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
        assert "positive" in r.json()["detail"]

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


class TestDealsEndpoints:
    def test_get_deals(self, client, mock_session):
        # Empty deals — just verify endpoint responds
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        r = client.get("/deals")
        assert r.status_code == 200
        assert r.json() == []


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
