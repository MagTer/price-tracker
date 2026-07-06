"""Tests for MCP server tools and bearer-token auth."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
            return_value=[
                {
                    "store_name": "Apotea",
                    "price_sek": 149.0,
                    "offer_price_sek": None,
                    "in_stock": True,
                }
            ]
        )
        mock_get_svc.return_value = mock_service

        result = await check_price("omega-3")
        assert "Apotea Omega-3" in result
        assert "149.00 kr" in result

    @patch("mcp_server.server._get_service")
    async def test_check_price_not_found(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(return_value=[])
        mock_get_svc.return_value = mock_service

        result = await check_price("nonexistent")
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

        result = await find_deals("grocery")
        assert "Mjolk" in result
        assert "10.00 kr" in result

    @patch("mcp_server.server._get_service")
    async def test_compare_stores(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.get_products = AsyncMock(
            return_value=[{"id": "p1", "name": "Ost"}]
        )
        mock_service.get_price_history = AsyncMock(
            return_value=[
                {
                    "store_name": "ICA",
                    "price_sek": 45.0,
                    "offer_price_sek": None,
                    "unit_price_sek": None,
                    "in_stock": True,
                },
                {
                    "store_name": "Willys",
                    "price_sek": 39.0,
                    "offer_price_sek": None,
                    "unit_price_sek": None,
                    "in_stock": True,
                },
            ]
        )
        mock_get_svc.return_value = mock_service

        result = await compare_stores("ost")
        assert "ICA" in result
        assert "Willys" in result
        assert "45.00 kr" in result
        assert "39.00 kr" in result

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
        mock_service.get_stores = AsyncMock(
            return_value=[{"id": "s1", "name": "ICA"}]
        )
        mock_get_svc.return_value = mock_service

        result = await list_products()
        assert "Toalettpapper" in result
        assert "ICA" in result
