"""Tests for WillysApiExtractor."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain.extractors.willys_api import WillysApiExtractor
from domain.result import PriceExtractionResult


def _make_extractor() -> WillysApiExtractor:
    return WillysApiExtractor()


def _valid_api_response(
    price_value: float = 29.90,
    compare_price: str = "33,29 kr",
    savings_amount: float = 0,
    out_of_stock: bool = False,
) -> dict[str, object]:
    data: dict[str, object] = {
        "priceValue": price_value,
        "comparePrice": compare_price,
        "outOfStock": out_of_stock,
    }
    if savings_amount:
        data["savingsAmount"] = savings_amount
    return data


# ---------------------------------------------------------------------------
# _extract_product_code
# ---------------------------------------------------------------------------


class TestExtractProductCode:
    def test_extract_product_code_from_standard_url(self) -> None:
        """Standard Willys URL returns product code."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Skogaholms-Limpa-100014716_ST"
        assert extractor._extract_product_code(url) == "100014716_ST"

    def test_extract_product_code_with_query_string(self) -> None:
        """URL with query string still extracts code."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST?queryParam=value"
        assert extractor._extract_product_code(url) == "100014716_ST"

    def test_extract_product_code_returns_none_for_non_willys(self) -> None:
        """Non-Willys URL returns None."""
        extractor = _make_extractor()
        url = "https://ica.se/product/123"
        assert extractor._extract_product_code(url) is None


# ---------------------------------------------------------------------------
# extract (HTTP layer)
# ---------------------------------------------------------------------------


def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict[str, object] | None = None,
) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestExtract:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        """Valid API response returns correct PriceExtractionResult."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        api_data = _valid_api_response(price_value=29.90, compare_price="14,95 kr")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(200, api_data))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await extractor.extract(url, "Mjolk")

        assert result is not None
        assert isinstance(result, PriceExtractionResult)
        assert result.price_sek == Decimal("29.9")
        assert result.store_unit_price_sek == Decimal("14.95")
        assert result.offer_price_sek is None
        assert result.in_stock is True
        assert result.confidence == 0.99
        assert result.raw_response.get("source") == "willys_api"

    @pytest.mark.asyncio
    async def test_extract_with_savings(self) -> None:
        """Response with savingsAmount sets offer fields and reconstructs regular price."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Toalettpapper-100014716_ST"
        # Current (offer) price is 19.90, savings are 10.00 -> regular = 29.90
        api_data = _valid_api_response(price_value=19.90, savings_amount=10.0)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(200, api_data))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await extractor.extract(url)

        assert result is not None
        assert result.offer_price_sek == Decimal("19.9")
        assert result.price_sek == Decimal("29.9")  # reconstructed regular price
        assert result.offer_type == "kampanj"
        assert result.offer_details is not None
        assert "10.0" in result.offer_details

    @pytest.mark.asyncio
    async def test_extract_out_of_stock(self) -> None:
        """outOfStock=true maps to in_stock=False."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        api_data = _valid_api_response(out_of_stock=True)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(200, api_data))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await extractor.extract(url)

        assert result is not None
        assert result.in_stock is False

    @pytest.mark.asyncio
    async def test_extract_returns_none_on_404(self) -> None:
        """HTTP 404 returns None to trigger LLM fallback."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(404))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await extractor.extract(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_returns_none_on_timeout(self) -> None:
        """httpx.TimeoutException returns None."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await extractor.extract(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_returns_none_for_invalid_url(self) -> None:
        """URL without product code pattern returns None immediately (no HTTP call)."""
        extractor = _make_extractor()
        url = "https://ica.se/product/123"

        with patch("httpx.AsyncClient") as mock_cls:
            result = await extractor.extract(url)

        assert result is None
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_response (unit tests)
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_parse_compare_price(self) -> None:
        """Compare price "33,29 kr" is correctly parsed to Decimal."""
        extractor = _make_extractor()
        data: dict[str, object] = {
            "priceValue": 49.90,
            "comparePrice": "33,29 kr",
            "outOfStock": False,
        }
        result = extractor._parse_response(data)
        assert result.store_unit_price_sek == Decimal("33.29")

    def test_parse_compare_price_with_per_kg_unit(self) -> None:
        """Compare price "33,29 kr/kg" strips the unit suffix before Decimal."""
        extractor = _make_extractor()
        data: dict[str, object] = {
            "priceValue": 49.90,
            "comparePrice": "33,29 kr/kg",
            "outOfStock": False,
        }
        result = extractor._parse_response(data)
        assert result.store_unit_price_sek == Decimal("33.29")

    def test_parse_compare_price_with_per_st_unit(self) -> None:
        """Compare price "12,50 kr/st" strips the unit suffix before Decimal."""
        extractor = _make_extractor()
        data: dict[str, object] = {
            "priceValue": 49.90,
            "comparePrice": "12,50 kr/st",
            "outOfStock": False,
        }
        result = extractor._parse_response(data)
        assert result.store_unit_price_sek == Decimal("12.50")

    def test_parse_missing_compare_price(self) -> None:
        """Missing comparePrice results in None store_unit_price_sek."""
        extractor = _make_extractor()
        data: dict[str, object] = {
            "priceValue": 29.90,
            "outOfStock": False,
        }
        result = extractor._parse_response(data)
        assert result.store_unit_price_sek is None

    def test_parse_no_savings_no_offer(self) -> None:
        """Response without savingsAmount has no offer fields."""
        extractor = _make_extractor()
        data: dict[str, object] = {
            "priceValue": 29.90,
            "comparePrice": "14,95 kr",
            "outOfStock": False,
        }
        result = extractor._parse_response(data)
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None


# ---------------------------------------------------------------------------
# JsonLdExtractor
# ---------------------------------------------------------------------------

from domain.extractors.jsonld import JsonLdExtractor  # noqa: E402


def _wrap_ldjson(payload: str) -> str:
    return (
        f'<html><head><script type="application/ld+json">{payload}</script></head>'
        f"<body>x</body></html>"
    )


class TestJsonLdExtractor:
    """Shapes verified against live store pages 2026-07-13."""

    def test_top_level_product_string_price(self) -> None:
        """ICA shape: top-level Product, price as string."""
        html = _wrap_ldjson(
            '{"@context":"https://schema.org","@type":"Product",'
            '"name":"Bad & Toalettpapper 16-p","offers":{"@type":"Offer",'
            '"price":"108.95","priceCurrency":"SEK",'
            '"availability":"https://schema.org/InStock"}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("108.95")
        assert result.in_stock is True
        assert result.confidence == 0.95
        assert result.raw_response["source"] == "jsonld"

    def test_top_level_product_numeric_price(self) -> None:
        """Apotea shape: price as JSON number."""
        html = _wrap_ldjson(
            '{"@context":"https://schema.org","@type":"Product","name":"Sukrin 500 g",'
            '"offers":{"@type":"Offer","price":69,"priceCurrency":"SEK",'
            '"availability":"https://schema.org/InStock"}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("69")

    def test_itempage_mainentity_wrapper(self) -> None:
        """DOZ shape: ItemPage wrapper with mainEntity Product."""
        html = _wrap_ldjson(
            '{"@context":"https://schema.org/","@type":"ItemPage",'
            '"mainEntity":{"@type":"Product","name":"Pevaryl",'
            '"offers":{"@type":"Offer","price":"133","priceCurrency":"SEK",'
            '"availability":"http://schema.org/InStock"}}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("133")

    def test_product_among_multiple_blocks(self) -> None:
        """Med24 shape: Product after non-Product blocks."""
        html = (
            '<script type="application/ld+json">{"@type":"WebSite","name":"x"}</script>'
            '<script type="application/ld+json">{"@type":"Product","name":"Kantskydd",'
            '"offers":{"@type":"Offer","price":"149.00","priceCurrency":"SEK"}}</script>'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("149.00")

    def test_out_of_stock(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x","offers":{"@type":"Offer","price":"10",'
            '"priceCurrency":"SEK","availability":"https://schema.org/OutOfStock"}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.in_stock is False

    def test_rejects_non_sek_currency(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x","offers":{"@type":"Offer","price":"10",'
            '"priceCurrency":"EUR"}}'
        )
        assert JsonLdExtractor().extract_from_html(html) is None

    def test_comma_decimal_price(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x","offers":{"@type":"Offer","price":"108,95",'
            '"priceCurrency":"SEK"}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("108.95")

    def test_offers_as_list(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x","offers":[{"@type":"Offer","price":"25",'
            '"priceCurrency":"SEK"}]}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("25")

    def test_returns_none_without_product(self) -> None:
        html = _wrap_ldjson('{"@type":"BreadcrumbList","itemListElement":[]}')
        assert JsonLdExtractor().extract_from_html(html) is None

    def test_returns_none_without_offers(self) -> None:
        html = _wrap_ldjson('{"@type":"Product","name":"x"}')
        assert JsonLdExtractor().extract_from_html(html) is None

    def test_tolerates_malformed_json_block(self) -> None:
        """A broken block is skipped; a later valid block is still used."""
        html = (
            '<script type="application/ld+json">{not json</script>'
            '<script type="application/ld+json">{"@type":"Product","name":"x",'
            '"offers":{"@type":"Offer","price":"10","priceCurrency":"SEK"}}</script>'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("10")

    def test_rejects_zero_price(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x","offers":{"@type":"Offer","price":0,'
            '"priceCurrency":"SEK"}}'
        )
        assert JsonLdExtractor().extract_from_html(html) is None

    def test_returns_none_on_plain_html(self) -> None:
        assert JsonLdExtractor().extract_from_html("<html><body>hej</body></html>") is None
