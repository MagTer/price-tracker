"""Tests for WillysApiExtractor."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.extractors.willys_api import WillysApiExtractor
from domain.result import PriceExtractionResult, StoreBlockedError


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


def _fetch_json_ok(data: dict[str, object]) -> dict[str, object]:
    """A successful WebFetcher.fetch_json result."""
    return {"ok": True, "data": data, "error": None, "blocked": False}


def _fetch_json_fail(error: str, *, blocked: bool = False) -> dict[str, object]:
    """A failed WebFetcher.fetch_json result (blocked=True for a bot wall)."""
    return {"ok": False, "data": None, "error": error, "blocked": blocked}


def _mock_fetcher(result: dict[str, object]) -> MagicMock:
    """The shared WebFetcher as the extractor sees it: one fetch_json call."""
    fetcher = MagicMock()
    fetcher.fetch_json = AsyncMock(return_value=result)
    return fetcher


class TestExtract:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        """Valid API response returns correct PriceExtractionResult."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        api_data = _valid_api_response(price_value=29.90, compare_price="14,95 kr")
        fetcher = _mock_fetcher(_fetch_json_ok(api_data))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
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
    async def test_extract_passes_the_page_url_as_referer(self) -> None:
        """The API call is repainted as a same-origin XHR, and a same-origin XHR with no
        Referer contradicts itself — the page it claims to come from is the product page
        being checked."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_ok(_valid_api_response(price_value=29.90)))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            await extractor.extract(url)

        assert fetcher.fetch_json.await_args.kwargs["referer"] == url

    @pytest.mark.asyncio
    async def test_extract_with_savings(self) -> None:
        """savingsAmount: priceValue is ordinarie, offer = priceValue - savings.

        Live shape (Bearnaise 101283524_ST, 2026-07-25): priceValue 21.29 is the ordinarie
        price, savingsAmount 3.39 is the per-unit discount, so the campaign price you pay is
        21.29 - 3.39 = 17.90 (which equals potentialPromotions[].price on the live page).
        """
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Bearnaise-Original-101283524_ST"
        api_data = _valid_api_response(price_value=21.29, savings_amount=3.39)
        fetcher = _mock_fetcher(_fetch_json_ok(api_data))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            result = await extractor.extract(url)

        assert result is not None
        assert result.price_sek == Decimal("21.29")  # ordinarie, unchanged
        assert result.offer_price_sek == Decimal("17.90")  # campaign price you pay now
        assert result.offer_type == "kampanj"
        assert result.offer_details is not None
        assert "3.39" in result.offer_details

    @pytest.mark.asyncio
    async def test_extract_out_of_stock(self) -> None:
        """outOfStock=true maps to in_stock=False."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_ok(_valid_api_response(out_of_stock=True)))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            result = await extractor.extract(url)

        assert result is not None
        assert result.in_stock is False

    @pytest.mark.asyncio
    async def test_extract_returns_none_on_404(self) -> None:
        """HTTP 404 returns None to trigger LLM fallback."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_fail("HTTP 404"))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            result = await extractor.extract(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_returns_none_on_timeout(self) -> None:
        """A network timeout (fetch_json ok=False) returns None."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_fail("timed out"))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            result = await extractor.extract(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_returns_none_for_invalid_url(self) -> None:
        """URL without product code pattern returns None immediately (no HTTP call)."""
        extractor = _make_extractor()
        url = "https://ica.se/product/123"
        fetcher = _mock_fetcher(_fetch_json_ok({}))

        with patch("infra.providers.get_fetcher", return_value=fetcher):
            result = await extractor.extract(url)

        assert result is None
        fetcher.fetch_json.assert_not_called()


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

    def test_offer_list_with_price_specification(self) -> None:
        """Apohem shape (verified 2026-07-22): offers is a LIST of Offer dicts, string price,
        ord.pris in a ListPrice priceSpecification (dict). Harmonized with Willys (v0.25.3):
        the ordinarie is price_sek, the current price is the flagged offer."""
        html = _wrap_ldjson(
            '{"@context":"http://schema.org/","@type":"Product",'
            '"name":"Elexir Pharma Omega-3 Forte 1000 mg 132 kapslar",'
            '"brand":{"type":"Thing","name":"Elexir Pharma"},'
            '"offers":[{"type":"Offer","availability":"http://schema.org/InStock",'
            '"price":"97","priceCurrency":"SEK",'
            '"priceSpecification":{"type":"UnitPriceSpecification",'
            '"priceType":"http://schema.org/ListPrice","price":"129",'
            '"priceCurrency":"SEK"}}]}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("129")  # ordinarie
        assert result.offer_price_sek == Decimal("97")  # campaign price paid
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 32 kr"
        assert result.in_stock is True

    def test_top_level_list_of_typed_objects(self) -> None:
        """Kronans Apotek shape (verified 2026-07-22): one block holding a LIST of typed
        objects, Product first, campaign price in offers.price with the ord.pris in a
        StrikethroughPrice priceSpecification (list). Harmonized with Willys (v0.25.3)."""
        html = _wrap_ldjson(
            '[{"@context":"https://schema.org","@type":"Product","@id":"#product",'
            '"name":"Elexir Pharma Omega-3 forte Kapslar 132 st",'
            '"brand":{"@type":"Brand","name":"Elexir Pharma"},'
            '"offers":{"@type":"Offer","price":102.75,"priceCurrency":"SEK",'
            '"priceSpecification":[{"@type":"UnitPriceSpecification",'
            '"priceType":"https://schema.org/StrikethroughPrice","price":137,'
            '"priceCurrency":"SEK"}],'
            '"availability":"https://schema.org/InStock"}},'
            '{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[]}]'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("137")  # ordinarie
        assert result.offer_price_sek == Decimal("102.75")  # campaign price paid
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 34.25 kr"
        assert result.in_stock is True

    def test_list_price_not_higher_is_not_a_sale(self) -> None:
        """A ListPrice equal to (or below) the current price is not a campaign — no offer.

        Guards against flagging every product with a priceSpecification as on sale."""
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x",'
            '"offers":{"@type":"Offer","price":"129","priceCurrency":"SEK",'
            '"priceSpecification":{"priceType":"https://schema.org/ListPrice",'
            '"price":"129","priceCurrency":"SEK"}}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("129")
        assert result.offer_price_sek is None
        assert result.offer_type is None

    def test_unit_price_specification_is_not_treated_as_ordinarie(self) -> None:
        """A UnitPriceSpecification carrying the jfr-pris (kr/kg) has no ListPrice/
        StrikethroughPrice priceType and must not be read as a struck-through ordinarie."""
        html = _wrap_ldjson(
            '{"@type":"Product","name":"x",'
            '"offers":{"@type":"Offer","price":"50","priceCurrency":"SEK",'
            '"priceSpecification":{"@type":"UnitPriceSpecification",'
            '"price":"200","priceCurrency":"SEK","referenceQuantity":'
            '{"@type":"QuantitativeValue","unitCode":"KGM"}}}}'
        )
        result = JsonLdExtractor().extract_from_html(html)
        assert result is not None
        assert result.price_sek == Decimal("50")
        assert result.offer_price_sek is None
        assert result.offer_type is None

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


class TestJsonLdNameSanity:
    """Zero token overlap between tracked name and node name -> None (LLM fallback).

    Guards against a recommendation carousel's Product node being recorded as the
    tracked product's price with 0.95 confidence.
    """

    CAROUSEL = _wrap_ldjson(
        '{"@type":"Product","name":"Nutrilett Smoothie Crush",'
        '"offers":{"@type":"Offer","price":"49.90","priceCurrency":"SEK"}}'
    )

    def test_carousel_name_mismatch_returns_none_and_warns(self, caplog) -> None:
        with caplog.at_level("WARNING", logger="domain.extractors.jsonld"):
            result = JsonLdExtractor().extract_from_html(
                self.CAROUSEL, product_name="Lambi Toalettpapper"
            )
        assert result is None
        warnings = [rec.getMessage() for rec in caplog.records]
        assert any(
            "Nutrilett Smoothie Crush" in msg and "Lambi Toalettpapper" in msg for msg in warnings
        )

    def test_token_overlap_passes_through(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"Lambi Bad & Toalettpapper 16-p",'
            '"offers":{"@type":"Offer","price":"108.95","priceCurrency":"SEK"}}'
        )
        result = JsonLdExtractor().extract_from_html(html, product_name="Lambi toalettpapper")
        assert result is not None
        assert result.price_sek == Decimal("108.95")

    def test_no_product_name_skips_the_check(self) -> None:
        result = JsonLdExtractor().extract_from_html(self.CAROUSEL, product_name=None)
        assert result is not None
        assert result.price_sek == Decimal("49.90")

    def test_empty_jsonld_name_skips_the_check(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"",'
            '"offers":{"@type":"Offer","price":"25","priceCurrency":"SEK"}}'
        )
        result = JsonLdExtractor().extract_from_html(html, product_name="Lambi Toalettpapper")
        assert result is not None
        assert result.price_sek == Decimal("25")

    def test_only_short_shared_tokens_count_as_no_overlap(self) -> None:
        """Tokens shorter than 3 chars ("3", "p", "st") fake an overlap on every title."""
        html = _wrap_ldjson(
            '{"@type":"Product","name":"Nezeril 3-p st",'
            '"offers":{"@type":"Offer","price":"79","priceCurrency":"SEK"}}'
        )
        result = JsonLdExtractor().extract_from_html(html, product_name="Lambi 3-p st")
        assert result is None

    def test_swedish_letters_participate_in_tokens(self) -> None:
        html = _wrap_ldjson(
            '{"@type":"Product","name":"Grov m\\u00f6rk r\\u00e5gbr\\u00f6d",'
            '"offers":{"@type":"Offer","price":"32","priceCurrency":"SEK"}}'
        )
        result = JsonLdExtractor().extract_from_html(html, product_name="Rågbröd grov")
        assert result is not None
        assert result.price_sek == Decimal("32")


class TestWillysApiIsThrottledAndBlockAware:
    """The Willys REST call is a real outgoing request and must be treated as one.

    It used to fire straight out of `httpx` with no politeness slot and DEBUG-only logging —
    so a Willys price check made TWO requests to www.willys.se (the page fetch, then this) on
    ONE reserved slot, and a bot wall was indistinguishable from a missing product.
    """

    @pytest.mark.asyncio
    async def test_the_api_call_spends_a_slot_on_the_shared_ledger(self) -> None:
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"

        limiter = AsyncMock()
        fetcher = _mock_fetcher(_fetch_json_ok(_valid_api_response(price_value=29.90)))

        with (
            patch("infra.providers.get_rate_limiter", return_value=limiter),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            await extractor.extract(url, "Mjolk")

        limiter.acquire.assert_awaited_once()
        # Keyed on the HOST, not a store id — the extractor never sees one, and the host is
        # what a WAF actually rate-limits.
        assert limiter.acquire.await_args.args[0] == "host:www.willys.se"
        # Interactive-grade cap: a background reservation must not stall a quick-add preview.
        assert limiter.acquire.await_args.kwargs["max_wait"] > 0

    @pytest.mark.asyncio
    async def test_a_malformed_url_costs_no_slot(self) -> None:
        """No product code means no request — do not spend politeness budget on nothing."""
        extractor = _make_extractor()
        limiter = AsyncMock()

        with patch("infra.providers.get_rate_limiter", return_value=limiter):
            result = await extractor.extract("https://www.willys.se/produkt/no-code-here", None)

        assert result is None
        limiter.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_api_call_goes_through_the_shared_browser_client(self) -> None:
        """The REST call rides WebFetcher's client — Chrome fingerprint, h2, shared TLS
        session — not a per-call httpx client announcing `python-httpx` over HTTP/1.1 to the
        very host the page fetch just spoke Chrome-h2 to."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_ok(_valid_api_response()))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            await extractor.extract(url, "Mjolk")

        fetcher.fetch_json.assert_awaited_once_with(
            "https://www.willys.se/axfood/rest/p/100014716_ST", referer=url
        )

    @pytest.mark.asyncio
    async def test_a_bot_wall_raises_store_blocked_for_both_paths(self) -> None:
        """A wall must STOP the ladder, not degrade to None: None means "product missing",
        which fell through to the LLM and ended the check as a strike-resetting no_price."""
        extractor = _make_extractor()
        url = "https://www.willys.se/produkt/Mjolk-100014716_ST"
        fetcher = _mock_fetcher(_fetch_json_fail("blocked (HTTP 403)", blocked=True))

        with (
            patch("infra.providers.get_rate_limiter", return_value=AsyncMock()),
            patch("infra.providers.get_fetcher", return_value=fetcher),
        ):
            with pytest.raises(StoreBlockedError):
                await extractor.extract(url, "Mjolk")
            with pytest.raises(StoreBlockedError):
                await extractor.extract_metadata(url)
