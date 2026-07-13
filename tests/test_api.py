"""Tests for FastAPI admin endpoints and auth."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import require_auth
from infra.db import async_session_factory


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
    def test_create_product(self, client):
        mock_service = MagicMock()
        mock_product = MagicMock()
        mock_product.id = "prod-1"
        mock_service.create_product = AsyncMock(return_value=mock_product)

        from api.admin import get_price_tracker_service as admin_get_service
        client.app.dependency_overrides[admin_get_service] = lambda: mock_service

        r = client.post(
            "/products",
            json={
                "tenant_id": "f21b6620-c793-46e3-a354-dfcd9956b4a2",
                "name": "Test Product",
                "brand": "TestBrand",
                "category": "TestCat",
                "unit": "st",
                "package_size": "24-pack",
                "package_quantity": 24,
            },
        )
        assert r.status_code == 201
        assert r.json()["product_id"] == "prod-1"

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

    def test_delete_product(self, client):
        mock_service = MagicMock()
        mock_service.delete_product = AsyncMock(return_value=None)

        from api.admin import get_price_tracker_service as admin_get_service
        client.app.dependency_overrides[admin_get_service] = lambda: mock_service

        r = client.delete("/products/prod-1")
        assert r.status_code == 200
        assert r.json()["message"] == "Product deleted successfully"


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
