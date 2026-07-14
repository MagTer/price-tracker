"""Tests for MCP server tools and bearer-token auth."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_server.auth import BearerTokenMiddleware
from mcp_server.server import check_price, compare_stores, find_deals, list_products


class TestBearerTokenMiddleware:
    async def test_missing_token_rejected(self):
        calls = []

        async def app(scope, receive, send):
            calls.append("app")

        middleware = BearerTokenMiddleware(app, "secret123")

        responses = []

        async def mock_send(msg):
            responses.append(msg)

        scope = {"type": "http", "headers": []}
        await middleware(scope, None, mock_send)

        assert len(responses) == 2
        assert responses[0]["status"] == 401
        assert "app" not in calls

    async def test_wrong_token_rejected(self):
        async def app(scope, receive, send):
            pass

        middleware = BearerTokenMiddleware(app, "secret123")
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer wrong")],
        }
        await middleware(scope, None, mock_send)
        assert responses[0]["status"] == 401

    async def test_valid_token_allowed(self):
        calls = []

        async def app(scope, receive, send):
            calls.append("app")

        middleware = BearerTokenMiddleware(app, "secret123")
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer secret123")],
        }
        await middleware(scope, None, lambda x: None)
        assert "app" in calls

    async def test_non_http_passes_through(self):
        calls = []

        async def app(scope, receive, send):
            calls.append("app")

        middleware = BearerTokenMiddleware(app, "secret123")
        scope = {"type": "websocket", "headers": []}
        await middleware(scope, None, lambda x: None)
        assert "app" in calls


def _link(**overrides):
    """A get_links_for_product() row — every key the service actually emits (04.1-03)."""
    row = {
        "product_store_id": "ps1",
        "store_id": "s1",
        "store_name": "Willys",
        "store_slug": "willys",
        "store_url": "https://willys.se/lambi-24",
        "package_size": "24-pack",
        "package_quantity": 24.0,
        "scraped_package_quantity": None,
        "price_sek": 139.90,
        "offer_price_sek": None,
        "store_unit_price_sek": 5.83,
        "unit_price_sek": 5.83,
        "in_stock": True,
        "checked_at": datetime(2026, 7, 14, 6, 0, tzinfo=UTC),
        "needs_amount": False,
        "quantity_mismatch": False,
    }
    row.update(overrides)
    return row


def _history_row(**overrides):
    """A get_price_history() row — every key the service actually emits (04.1-03)."""
    row = {
        "checked_at": datetime(2026, 7, 14, 6, 0, tzinfo=UTC),
        "product_store_id": "ps1",
        "store_name": "Apotea",
        "store_slug": "apotea",
        "package_size": None,
        "package_quantity": None,
        "price_sek": 149.0,
        "offer_price_sek": None,
        "store_unit_price_sek": None,
        "unit_price_sek": None,
        "in_stock": True,
    }
    row.update(overrides)
    return row


# The canonical Lambi scenario, in the order get_links_for_product() returns it:
# ranked by the COMPUTED kr/rulle, with the link that still needs an amount last.
# The Coop row deliberately carries a store-printed kr/100g value — the reason the
# printed number can never be the sort key (D-05).
LAMBI_LINKS = [
    _link(
        product_store_id="ps1",
        store_id="s1",
        store_name="Willys",
        store_slug="willys",
        store_url="https://willys.se/lambi-24",
        package_size="24-pack",
        package_quantity=24.0,
        price_sek=139.90,
        store_unit_price_sek=5.83,
        unit_price_sek=5.83,
    ),
    _link(
        product_store_id="ps2",
        store_id="s2",
        store_name="ICA",
        store_slug="ica",
        store_url="https://ica.se/lambi-8",
        package_size="8-pack",
        package_quantity=8.0,
        price_sek=59.90,
        store_unit_price_sek=8.10,
        unit_price_sek=7.49,
    ),
    _link(
        product_store_id="ps3",
        store_id="s3",
        store_name="Coop",
        store_slug="coop",
        store_url="https://coop.se/lambi",
        package_size=None,
        package_quantity=None,
        price_sek=129.0,
        store_unit_price_sek=12.90,
        unit_price_sek=None,
        needs_amount=True,
    ),
]


class TestMcpTools:
    @patch("mcp_server.server._get_service")
    async def test_check_price_found(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[
                {
                    "id": "p1",
                    "name": "Apotea Omega-3",
                    "brand": "Apotea",
                }
            ]
        )
        mock_service.get_price_history = AsyncMock(
            return_value=[_history_row(price_sek=149.0, in_stock=True)]
        )
        mock_get_svc.return_value = mock_service

        result = await check_price.fn("omega-3")
        assert "Apotea Omega-3" in result
        assert "149.00 kr" in result

    @patch("mcp_server.server._get_service")
    async def test_check_price_reports_real_stock(self, mock_get_svc):
        """`in_stock` was a dead key until 04.1-03 — every product read "slut i lager"."""
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Lambi", "brand": "Lambi"}]
        )
        mock_service.get_price_history = AsyncMock(
            return_value=[_history_row(store_name="Willys", in_stock=True)]
        )
        mock_get_svc.return_value = mock_service

        result = await check_price.fn("lambi")
        assert "i lager" in result
        assert "slut i lager" not in result

    @patch("mcp_server.server._get_service")
    async def test_check_price_keeps_two_links_at_one_store_apart(self, mock_get_svc):
        """Deduping on store NAME would have shown only one of these two packs."""
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Lambi", "brand": "Lambi"}]
        )
        mock_service.get_price_history = AsyncMock(
            return_value=[
                _history_row(
                    product_store_id="ps1",
                    store_name="Willys",
                    package_size="24-pack",
                    price_sek=139.90,
                ),
                _history_row(
                    product_store_id="ps2",
                    store_name="Willys",
                    package_size="8-pack",
                    price_sek=59.90,
                ),
            ]
        )
        mock_get_svc.return_value = mock_service

        result = await check_price.fn("lambi")
        assert "139.90 kr" in result
        assert "59.90 kr" in result
        assert "24-pack" in result
        assert "8-pack" in result

    @patch("mcp_server.server._get_service")
    async def test_check_price_not_found(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(return_value=[])
        mock_get_svc.return_value = mock_service

        result = await check_price.fn("nonexistent")
        assert "Inga produkter" in result

    @patch("mcp_server.server._get_service")
    async def test_find_deals(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_current_deals = AsyncMock(
            return_value=[
                {
                    "product_name": "Mjolk",
                    "store_name": "Willys",
                    "regular_price_sek": 15.0,
                    "offer_price_sek": 10.0,
                    "offer_type": "extrapris",
                }
            ]
        )
        mock_get_svc.return_value = mock_service

        result = await find_deals.fn("grocery")
        assert "Mjolk" in result
        assert "10.00 kr" in result

    @patch("mcp_server.server._get_service")
    async def test_compare_stores_one_row_per_link_ranked_by_unit_price(self, mock_get_svc):
        """The Lambi scenario: three links, cheapest-per-roll first (D-13)."""
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Lambi Toalettpapper", "unit": "st"}]
        )
        mock_service.get_links_for_product = AsyncMock(return_value=LAMBI_LINKS)
        mock_get_svc.return_value = mock_service

        result = await compare_stores.fn("lambi")
        rows = [ln for ln in result.splitlines() if ln.startswith("| ") and " kr" in ln]

        # One row per LINK — three links, three rows (not one row per store).
        assert len(rows) == 3

        # The service's ranking is preserved: the tool renders, it does not re-sort.
        assert rows[0].startswith("| Willys | 24-pack")
        assert rows[1].startswith("| ICA | 8-pack")

        # Computed and store-printed values live in DIFFERENT columns (D-05).
        header = next(ln for ln in result.splitlines() if "Jämförspris" in ln)
        assert "Butiken anger" in header
        cols = [c.strip() for c in header.strip("|").split("|")]
        computed_col = cols.index("Jämförspris (kr/st)")
        printed_col = cols.index("Butiken anger")
        assert computed_col != printed_col

        # The kr/unit column shows a real number — it printed N/A for EVERY row from
        # extraction until this plan, because the key it read never existed.
        willys_cells = [c.strip() for c in rows[0].strip("|").split("|")]
        ica_cells = [c.strip() for c in rows[1].strip("|").split("|")]
        assert willys_cells[computed_col] == "5.83 kr"
        assert ica_cells[computed_col] == "7.49 kr"

        # ICA's row carries both: computed 7.49 beside the store's printed 8.10.
        assert ica_cells[printed_col] == "8.10 kr"
        assert ica_cells[computed_col] != ica_cells[printed_col]

        # The package label appears for the links that have one.
        assert "24-pack" in result
        assert "8-pack" in result

    @patch("mcp_server.server._get_service")
    async def test_compare_stores_marks_link_without_amount(self, mock_get_svc):
        """A NULL package_quantity renders a marker and does not crash (MODEL-07, D-02)."""
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Lambi Toalettpapper", "unit": "st"}]
        )
        mock_service.get_links_for_product = AsyncMock(return_value=LAMBI_LINKS)
        mock_get_svc.return_value = mock_service

        result = await compare_stores.fn("lambi")
        rows = [ln for ln in result.splitlines() if ln.startswith("| ") and " kr" in ln]

        # The un-priced link sorts last (the service put it there) and says what is wrong.
        assert rows[2].startswith("| Coop |")
        assert "saknar mängd" in rows[2]

    @patch("mcp_server.server._get_service")
    async def test_compare_stores_two_links_same_store(self, mock_get_svc):
        """The phase's acceptance scenario at the MCP boundary.

        The old store-name dedupe would have dropped one of these rows entirely.
        """
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Lambi Toalettpapper", "unit": "st"}]
        )
        mock_service.get_links_for_product = AsyncMock(
            return_value=[
                _link(
                    product_store_id="ps1",
                    store_name="Willys",
                    package_size="24-pack",
                    package_quantity=24.0,
                    price_sek=139.90,
                    unit_price_sek=5.83,
                ),
                _link(
                    product_store_id="ps2",
                    store_name="Willys",
                    store_url="https://willys.se/lambi-8",
                    package_size="8-pack",
                    package_quantity=8.0,
                    price_sek=59.90,
                    unit_price_sek=7.49,
                ),
            ]
        )
        mock_get_svc.return_value = mock_service

        result = await compare_stores.fn("lambi")
        rows = [ln for ln in result.splitlines() if ln.startswith("| Willys")]

        assert len(rows) == 2
        assert "24-pack" in rows[0]
        assert "8-pack" in rows[1]

    @patch("mcp_server.server._get_service")
    async def test_compare_stores_not_found(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(return_value=[])
        mock_get_svc.return_value = mock_service

        result = await compare_stores.fn("nonexistent")
        assert "Inga produkter" in result

    @patch("mcp_server.server._get_service")
    async def test_list_products(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[
                {
                    "id": "p1",
                    "name": "Toalettpapper",
                    "brand": "Lambi",
                    "category": "Hushall",
                    "stores": [{"store_id": "s1", "store_name": "ICA"}],
                }
            ]
        )
        mock_service.get_stores = AsyncMock(return_value=[{"id": "s1", "name": "ICA"}])
        mock_get_svc.return_value = mock_service

        result = await list_products.fn()
        assert "Toalettpapper" in result
        assert "ICA" in result


class TestFailClosed:
    async def test_empty_token_returns_503(self):
        """No configured token -> 503 for every request (fail closed)."""
        calls = []

        async def app(scope, receive, send):
            calls.append("app")

        middleware = BearerTokenMiddleware(app, "")
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer anything")],
        }
        await middleware(scope, None, mock_send)

        assert responses[0]["status"] == 503
        assert "app" not in calls

    async def test_get_mcp_app_always_wraps(self):
        """get_mcp_app never returns an unwrapped app, even without a token."""
        from unittest.mock import patch as _patch

        import mcp_server.server as server_mod

        with _patch.object(server_mod, "MCP_BEARER_TOKEN", ""):
            wrapped, http_app = server_mod.get_mcp_app()

        assert isinstance(wrapped, BearerTokenMiddleware)
        assert wrapped.token == ""
