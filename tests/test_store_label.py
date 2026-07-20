"""Tests for the per-link store label (ICA per-butik pricing).

Two ICA butiker on one product are two LINKS sharing the chain-level Store row — the data
model has handled that since 04.1, but every display surface said "ICA" for both. The label
fixes the display side: `link_store_name` (label wins, chain name is the fallback) is the
single rule, and these tests pin it at the model, payload, endpoint, and quick-add tiers.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.admin import _link_payload
from api.app import create_app
from api.auth import require_auth
from domain.models import Product, ProductStore, Store, link_store_name
from domain.quickadd import suggest_store_label

ICA_MAXI_URL = (
    "https://handlaprivatkund.ica.se/stores/1003396/products/potatissallad-200g-ica/1445862"
)
ICA_BJORKSATRA_URL = (
    "https://handlaprivatkund.ica.se/stores/1004503/products/"
    "sm%C3%B6r-raps-normalsaltat-75-500g-bregott/2129122"
)


# --- Pure tier -------------------------------------------------------------------------


class TestSuggestStoreLabel:
    def test_known_butik_ids_map_to_their_names(self):
        assert suggest_store_label(ICA_MAXI_URL, "ICA") == "ICA Maxi Sandviken"
        assert suggest_store_label(ICA_BJORKSATRA_URL, "ICA") == "ICA Supermarket Björksätra"

    def test_unknown_butik_id_still_distinguishes(self):
        url = "https://handlaprivatkund.ica.se/stores/9999999/products/x/1"
        assert suggest_store_label(url, "ICA") == "ICA 9999999"

    def test_url_without_store_segment_is_none(self):
        """Nationally priced chains (Willys, the pharmacies) need no label."""
        assert suggest_store_label("https://www.willys.se/produkt/x-123_ST", "Willys") is None
        assert suggest_store_label("https://www.apotea.se/alvedon", "Apotea") is None


def _store(name="ICA"):
    return Store(
        id=uuid.uuid4(),
        name=name,
        slug="ica",
        store_type="grocery",
        base_url="https://handlaprivatkund.ica.se",
        is_active=True,
    )


def _link(store, label=None, url=ICA_MAXI_URL):
    return ProductStore(
        id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        store_id=store.id,
        store_url=url,
        check_frequency_hours=72,
        store_label=label,
    )


class TestLinkStoreName:
    def test_label_wins(self):
        store = _store()
        assert link_store_name(_link(store, label="ICA Maxi Sandviken"), store) == (
            "ICA Maxi Sandviken"
        )

    def test_no_label_falls_back_to_chain_name(self):
        store = _store()
        assert link_store_name(_link(store, label=None), store) == "ICA"


class TestLinkPayloadCarriesBothNames:
    def test_display_name_is_the_label_and_raw_label_rides_along(self):
        store = _store()
        link = _link(store, label="ICA Supermarket Björksätra")
        payload = _link_payload(link, store, None)
        assert payload["store_name"] == "ICA Supermarket Björksätra"
        assert payload["store_label"] == "ICA Supermarket Björksätra"

    def test_unlabeled_link_displays_chain_and_null_label(self):
        store = _store()
        payload = _link_payload(_link(store), store, None)
        assert payload["store_name"] == "ICA"
        assert payload["store_label"] is None


# --- Endpoint tier ---------------------------------------------------------------------


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def client(mock_session):
    app = create_app()

    async def override_auth():
        return "test@example.com"

    async def override_get_db():
        yield mock_session

    from api.admin import get_db as admin_get_db

    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[admin_get_db] = override_get_db
    return TestClient(app)


@pytest.fixture
def mock_service(client):
    from api.admin import get_price_tracker_service as admin_get_service

    service = MagicMock()
    product = MagicMock()
    product.id = uuid.uuid4()
    service.create_product = AsyncMock(return_value=product)
    link = MagicMock()
    link.id = uuid.uuid4()
    service.link_product_store = AsyncMock(return_value=link)
    service.delete_product = AsyncMock(return_value=None)
    client.app.dependency_overrides[admin_get_service] = lambda: service
    return service


class TestLinkEndpointPassesLabel:
    def test_label_reaches_the_service(self, client, mock_service):
        r = client.post(
            f"/products/{uuid.uuid4()}/stores",
            json={
                "store_id": str(uuid.uuid4()),
                "store_url": ICA_MAXI_URL,
                "store_label": "  ICA Maxi Sandviken  ",
            },
        )
        assert r.status_code == 201
        kwargs = mock_service.link_product_store.await_args.kwargs
        assert kwargs["store_label"] == "ICA Maxi Sandviken"  # stripped

    def test_empty_label_becomes_null(self, client, mock_service):
        r = client.post(
            f"/products/{uuid.uuid4()}/stores",
            json={
                "store_id": str(uuid.uuid4()),
                "store_url": ICA_MAXI_URL,
                "store_label": "   ",
            },
        )
        assert r.status_code == 201
        assert mock_service.link_product_store.await_args.kwargs["store_label"] is None


class TestPackagingEndpointEditsLabel:
    def _put(self, client, mock_session, link, body):
        result = MagicMock()
        result.scalar_one_or_none.return_value = link
        mock_session.execute.return_value = result
        return client.put(f"/product-stores/{link.id}/packaging", json=body)

    def test_sets_the_label(self, client, mock_session):
        store = _store()
        link = _link(store)
        r = self._put(client, mock_session, link, {"store_label": "ICA Maxi Sandviken"})
        assert r.status_code == 200
        assert link.store_label == "ICA Maxi Sandviken"
        assert r.json()["store_label"] == "ICA Maxi Sandviken"

    def test_empty_string_clears_back_to_chain_fallback(self, client, mock_session):
        store = _store()
        link = _link(store, label="ICA Maxi Sandviken")
        r = self._put(client, mock_session, link, {"store_label": ""})
        assert r.status_code == 200
        assert link.store_label is None

    def test_omitted_field_leaves_the_label_untouched(self, client, mock_session):
        store = _store()
        link = _link(store, label="ICA Maxi Sandviken")
        r = self._put(client, mock_session, link, {"package_size": "200 g"})
        assert r.status_code == 200
        assert link.store_label == "ICA Maxi Sandviken"


_ICA_JSONLD_HTML = """<script type="application/ld+json">
{"@type":"Product","name":"Potatissallad 200g ICA",
 "offers":{"@type":"Offer","price":"24,90","priceCurrency":"SEK"}}
</script>"""


def _result(scalars_all=None, first=None):
    r = MagicMock()
    r.scalars.return_value.all.return_value = scalars_all or []
    r.first.return_value = first
    return r


class TestQuickAddSuggestsLabel:
    def test_preview_suggests_the_butik_label_for_ica(self, client, mock_session):
        mock_session.execute.side_effect = [
            _result(scalars_all=[_store()]),
            _result(first=None),
            _result(scalars_all=[]),
        ]
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={"ok": True, "text": "page", "html": _ICA_JSONLD_HTML, "error": None}
        )
        with patch("api.admin.get_fetcher", return_value=fetcher):
            r = client.post("/quick-add/preview", json={"url": ICA_MAXI_URL})
        assert r.status_code == 200
        assert r.json()["suggested_store_label"] == "ICA Maxi Sandviken"

    def test_confirm_passes_the_label_to_the_service(self, client, mock_session, mock_service):
        mock_session.execute.return_value = _result(first=None)
        r = client.post(
            "/quick-add",
            json={
                "url": ICA_BJORKSATRA_URL,
                "store_id": str(uuid.uuid4()),
                "name": "Bregott 500 g",
                "unit": "kg",
                "package_quantity": 0.5,
                "store_label": "ICA Supermarket Björksätra",
                "run_first_check": False,
            },
        )
        assert r.status_code == 201
        kwargs = mock_service.link_product_store.await_args.kwargs
        assert kwargs["store_label"] == "ICA Supermarket Björksätra"


class TestHistorySeriesAreDistinguishable:
    """The price-history route must label two ICA-butik links differently (admin.py's own
    query — CLAUDE.md Gotcha 4 — so this pins the route, not the service twin)."""

    def test_two_butik_links_get_two_names(self, client, mock_session):
        from datetime import datetime

        from domain.models import PricePoint

        store = _store()
        product = Product(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Potatissallad", unit="kg")
        link_a = _link(store, label="ICA Maxi Sandviken")
        link_b = _link(store, label="ICA Supermarket Björksätra", url=ICA_BJORKSATRA_URL)
        pp = lambda ps: PricePoint(  # noqa: E731
            id=uuid.uuid4(),
            product_store_id=ps.id,
            price_sek=Decimal("24.90"),
            in_stock=True,
            checked_at=datetime(2026, 7, 20, 6, 0),
        )

        # One query; tuple order is (PricePoint, Store, ProductStore).
        rows_result = MagicMock()
        rows_result.all.return_value = [
            (pp(link_a), store, link_a),
            (pp(link_b), store, link_b),
        ]
        mock_session.execute.return_value = rows_result

        r = client.get(f"/products/{product.id}/prices")
        assert r.status_code == 200
        names = {row["store_name"] for row in r.json()}
        assert names == {"ICA Maxi Sandviken", "ICA Supermarket Björksätra"}
