"""Tests for the price-extraction ladder (store API → store page-state → JSON-LD).

What happens when every tier declines, or a store says the product is gone, lives in
tests/test_ladder_without_llm.py beside the decision that removed the model tier.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.parser import PriceExtractionResult, PriceParser
from domain.result import StoreBlockedError, extraction_source

_WILLYS_URL = "https://www.willys.se/produkt/Mjolk-100014716_ST"
_RUSTA_URL = "https://www.rusta.com/sv-se/x/hangstol-sorrento-605011720101"

# JSON-LD with the CURRENT-only price a Rusta rea publishes — the page-state tier must win.
_RUSTA_JSONLD = (
    '<script type="application/ld+json">{"@context": "http://schema.org/",'
    '"@type": "Product", "name": "Hängstol Sorrento", "offers": [{"@type": "Offer",'
    '"price": "1899.00", "priceCurrency": "SEK"}]}</script>'
)


def _result(price: str, source: str, offer: str | None = None) -> PriceExtractionResult:
    return PriceExtractionResult(
        price_sek=Decimal(price),
        store_unit_price_sek=None,
        offer_price_sek=Decimal(offer) if offer is not None else None,
        offer_type="kampanj" if offer is not None else None,
        offer_details=None,
        in_stock=True,
        confidence=0.98,
        pack_size=None,
        package_amount=None,
        package_unit=None,
        raw_response={"source": source},
    )


class TestPriceExtractionResult:
    """Tests for PriceExtractionResult dataclass."""

    def test_create_with_all_fields(self) -> None:
        result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            store_unit_price_sek=Decimal("149.50"),
            offer_price_sek=Decimal("19.90"),
            offer_type="stammispris",
            offer_details="Köp 2 betala för 1",
            in_stock=True,
            confidence=0.95,
            pack_size=16,
            package_amount=Decimal("400"),
            package_unit="g",
            raw_response={"price": 29.90, "in_stock": True},
        )

        assert result.price_sek == Decimal("29.90")
        assert result.store_unit_price_sek == Decimal("149.50")
        assert result.offer_price_sek == Decimal("19.90")
        assert result.offer_type == "stammispris"
        assert result.offer_details == "Köp 2 betala för 1"
        assert result.in_stock is True
        assert result.confidence == 0.95
        assert result.pack_size == 16
        assert result.package_amount == Decimal("400")
        assert result.package_unit == "g"
        assert result.raw_response == {"price": 29.90, "in_stock": True}


class TestLadderOrder:
    @pytest.mark.asyncio
    async def test_the_store_api_answers_first(self) -> None:
        parser = PriceParser()
        api_result = _result("29.90", "willys_api")
        api = MagicMock()
        api.extract = AsyncMock(return_value=api_result)
        parser._api_extractors["willys"] = api

        result = await parser.extract_price(
            text_content="page content",
            store_slug="willys",
            product_name="Mjolk",
            store_url=_WILLYS_URL,
        )

        api.extract.assert_called_once()
        assert result == api_result

    @pytest.mark.asyncio
    async def test_an_api_miss_tries_the_page(self) -> None:
        parser = PriceParser()
        api = MagicMock()
        api.extract = AsyncMock(return_value=None)
        parser._api_extractors["willys"] = api
        html = (
            '<script type="application/ld+json">{"@type":"Product","name":"Mjolk",'
            '"offers":{"@type":"Offer","price":"18.50","priceCurrency":"SEK"}}</script>'
        )

        result = await parser.extract_price(
            text_content="page content",
            store_slug="willys",
            product_name="Mjolk",
            store_url=_WILLYS_URL,
            html_content=html,
        )

        assert result.price_sek == Decimal("18.50")
        assert extraction_source(result) == "jsonld"

    @pytest.mark.asyncio
    async def test_without_a_store_url_the_api_is_not_asked(self) -> None:
        parser = PriceParser()
        api = MagicMock()
        api.extract = AsyncMock(return_value=None)
        parser._api_extractors["willys"] = api

        result = await parser.extract_price(
            text_content="page content", store_slug="willys", product_name="Mjolk"
        )

        api.extract.assert_not_called()
        assert result.price_sek is None

    @pytest.mark.asyncio
    async def test_a_bot_wall_stops_the_ladder(self) -> None:
        """StoreBlockedError must pass THROUGH the API step's except-Exception fallback.

        Falling back would run JSON-LD on an empty SPA shell, and the caller would record a
        routine "no price" — a breaker-resetting success against a host walling us.
        """
        parser = PriceParser()
        api = MagicMock()
        api.extract = AsyncMock(side_effect=StoreBlockedError("Willys API wall"))
        parser._api_extractors["willys"] = api

        with pytest.raises(StoreBlockedError):
            await parser.extract_price(
                text_content="page content",
                store_slug="willys",
                product_name="Mjolk",
                store_url=_WILLYS_URL,
            )

    @pytest.mark.asyncio
    async def test_page_state_outranks_jsonld(self) -> None:
        """On a Rusta rea the JSON-LD carries only the campaign price, so letting it win
        would record the offer as ordinarie."""
        parser = PriceParser()
        page_result = _result("2999", "rusta_page", offer="1899")
        html_extractor = MagicMock()
        html_extractor.extract_from_html = MagicMock(return_value=page_result)
        parser._html_extractors["rusta"] = html_extractor

        result = await parser.extract_price(
            text_content="page content",
            store_slug="rusta",
            product_name="Hängstol Sorrento",
            store_url=_RUSTA_URL,
            html_content=_RUSTA_JSONLD,
        )

        html_extractor.extract_from_html.assert_called_once()
        assert result == page_result

    @pytest.mark.asyncio
    async def test_a_page_state_miss_falls_back_to_jsonld(self) -> None:
        """A CURRENT_PAGE the extractor cannot use degrades to JSON-LD (right price outside
        campaigns), never to a failed check."""
        parser = PriceParser()
        html_extractor = MagicMock()
        html_extractor.extract_from_html = MagicMock(return_value=None)
        parser._html_extractors["rusta"] = html_extractor

        result = await parser.extract_price(
            text_content="page content",
            store_slug="rusta",
            product_name="Hängstol Sorrento",
            store_url=_RUSTA_URL,
            html_content=_RUSTA_JSONLD,
        )

        assert result.price_sek == Decimal("1899.00")
        assert extraction_source(result) == "jsonld"

    @pytest.mark.asyncio
    async def test_a_raising_page_state_extractor_falls_back_to_jsonld(self) -> None:
        parser = PriceParser()
        html_extractor = MagicMock()
        html_extractor.extract_from_html = MagicMock(side_effect=ValueError("shape changed"))
        parser._html_extractors["rusta"] = html_extractor

        result = await parser.extract_price(
            text_content="page content",
            store_slug="rusta",
            product_name="Hängstol Sorrento",
            store_url=_RUSTA_URL,
            html_content=_RUSTA_JSONLD,
        )

        assert extraction_source(result) == "jsonld"
