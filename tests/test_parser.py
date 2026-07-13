"""Tests for price parser module."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.parser import PriceExtractionResult, PriceParser


class TestPriceExtractionResult:
    """Tests for PriceExtractionResult dataclass."""

    def test_create_with_all_fields(self) -> None:
        """Test creating result with all fields populated."""
        result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=Decimal("149.50"),
            offer_price_sek=Decimal("19.90"),
            offer_type="stammispris",
            offer_details="Köp 2 betala för 1",
            in_stock=True,
            confidence=0.95,
            pack_size=16,
            raw_response={"price": 29.90, "in_stock": True},
        )

        assert result.price_sek == Decimal("29.90")
        assert result.unit_price_sek == Decimal("149.50")
        assert result.offer_price_sek == Decimal("19.90")
        assert result.offer_type == "stammispris"
        assert result.offer_details == "Köp 2 betala för 1"
        assert result.in_stock is True
        assert result.confidence == 0.95
        assert result.pack_size == 16
        assert result.raw_response == {"price": 29.90, "in_stock": True}

    def test_create_with_none_values(self) -> None:
        """Test creating result with None values."""
        result = PriceExtractionResult(
            price_sek=None,
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=False,
            confidence=0.3,
            pack_size=None,
            raw_response={},
        )

        assert result.price_sek is None
        assert result.unit_price_sek is None
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None
        assert result.in_stock is False
        assert result.confidence == 0.3
        assert result.pack_size is None


class TestPriceParser:
    """Tests for PriceParser class."""

    def test_build_prompt_without_product_name(self) -> None:
        """Test _build_prompt method without product name."""
        parser = PriceParser()
        text = "Product page content here..."
        store_slug = "ica-maxi"
        store_hint = "Look for 'pris' field"

        prompt = parser._build_prompt(text, store_slug, store_hint, None)

        assert "ica-maxi" in prompt
        assert "Look for 'pris' field" in prompt
        assert "Product page content here..." in prompt
        assert "Product being searched:" not in prompt
        assert "Return a JSON object" in prompt

    def test_build_prompt_with_product_name(self) -> None:
        """Test _build_prompt method with product name."""
        parser = PriceParser()
        text = "Product page content here..."
        store_slug = "willys"
        store_hint = "Check kampanj section"
        product_name = "Mjölk Arla Standard 3%"

        prompt = parser._build_prompt(text, store_slug, store_hint, product_name)

        assert "willys" in prompt
        assert "Check kampanj section" in prompt
        assert "Product being searched: Mjölk Arla Standard 3%" in prompt
        assert "Product page content here..." in prompt

    def test_build_prompt_truncates_long_content(self) -> None:
        """Test that _build_prompt truncates content beyond 6000 chars."""
        parser = PriceParser()
        text = "x" * 10000  # 10k characters
        store_slug = "coop"
        store_hint = ""

        prompt = parser._build_prompt(text, store_slug, store_hint, None)

        # Content should be truncated to 6000 chars
        assert "x" * 6000 in prompt
        assert len([line for line in prompt.split("\n") if "xxxx" in line][0]) <= 6010

    def test_load_store_hints(self) -> None:
        """Test that store hints are loaded on initialization."""
        with patch("domain.stores.get_store_hints") as mock_hints:
            mock_hints.return_value = {
                "ica-maxi": "ICA hint",
                "willys": "Willys hint",
            }

            parser = PriceParser()

            assert parser._store_hints == {
                "ica-maxi": "ICA hint",
                "willys": "Willys hint",
            }
            mock_hints.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_model_success(self) -> None:
        """Test _extract_with_model with successful extraction."""
        parser = PriceParser()

        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "price": 29.90,
                                "unit_price": 149.50,
                                "offer_price": None,
                                "offer_type": None,
                                "offer_details": None,
                                "in_stock": True,
                                "confidence": 0.95,
                            }
                        )
                    }
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await parser._extract_with_model("test prompt", "price_tracker")

            assert result.price_sek == Decimal("29.90")
            assert result.unit_price_sek == Decimal("149.50")
            assert result.offer_price_sek is None
            assert result.in_stock is True
            assert result.confidence == 0.95
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_model_strips_markdown_code_blocks(self) -> None:
        """Test _extract_with_model handles markdown code blocks."""
        parser = PriceParser()

        json_content = '```json\n{"price": 15.50, "in_stock": true, "confidence": 0.8}\n```'
        mock_response_data = {"choices": [{"message": {"content": json_content}}]}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await parser._extract_with_model("test prompt", "price_tracker_fallback")

            assert result.price_sek == Decimal("15.50")
            assert result.in_stock is True
            assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_extract_price_uses_primary_model_first(self) -> None:
        """Test that extract_price tries price_tracker model first."""
        parser = PriceParser()

        mock_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.85,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_result

            result = await parser.extract_price("page content", "ica-maxi", "Mjölk")

            # Should call with the primary model first
            mock_extract.assert_called_once()
            assert "llama-4-scout" in str(mock_extract.call_args)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_extract_price_retries_with_fallback_on_low_confidence(self) -> None:
        """Test that extract_price retries with fallback if primary confidence is low."""
        parser = PriceParser()

        primary_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.5,  # Below threshold
            pack_size=None,
            raw_response={},
        )

        fallback_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.95,  # High confidence
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            mock_extract.side_effect = [primary_result, fallback_result]

            result = await parser.extract_price("page content", "willys")

            # Should call twice: price_tracker first, then price_tracker_fallback
            assert mock_extract.call_count == 2
            assert result == fallback_result

    @pytest.mark.asyncio
    async def test_extract_price_falls_back_on_error(self) -> None:
        """Test that extract_price falls back if primary model fails."""
        parser = PriceParser()

        fallback_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.9,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            # Primary raises exception, fallback succeeds
            mock_extract.side_effect = [Exception("price_tracker error"), fallback_result]

            result = await parser.extract_price("page content", "coop")

            assert mock_extract.call_count == 2
            assert result == fallback_result

    @pytest.mark.asyncio
    async def test_extract_price_tries_api_extractor_first(self) -> None:
        """When store_url provided and slug has API extractor, API is called first.

        If API returns a result, _extract_with_model (LLM) is NOT called.
        """
        parser = PriceParser()

        api_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.99,
            pack_size=None,
            raw_response={"source": "willys_api"},
        )

        mock_api_extractor = AsyncMock()
        mock_api_extractor.extract = AsyncMock(return_value=api_result)
        parser._api_extractors["willys"] = mock_api_extractor

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            result = await parser.extract_price(
                text_content="page content",
                store_slug="willys",
                product_name="Mjolk",
                store_url="https://www.willys.se/produkt/Mjolk-100014716_ST",
            )

        mock_api_extractor.extract.assert_called_once()
        mock_llm.assert_not_called()
        assert result == api_result

    @pytest.mark.asyncio
    async def test_extract_price_falls_back_to_llm_when_api_returns_none(self) -> None:
        """API extractor returns None -> LLM cascade is used."""
        parser = PriceParser()

        mock_api_extractor = AsyncMock()
        mock_api_extractor.extract = AsyncMock(return_value=None)
        parser._api_extractors["willys"] = mock_api_extractor

        llm_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.85,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm_result

            result = await parser.extract_price(
                text_content="page content",
                store_slug="willys",
                product_name="Mjolk",
                store_url="https://www.willys.se/produkt/Mjolk-100014716_ST",
            )

        mock_api_extractor.extract.assert_called_once()
        mock_llm.assert_called_once()
        assert result == llm_result

    @pytest.mark.asyncio
    async def test_extract_price_without_store_url_skips_api(self) -> None:
        """No store_url -> API extractor is NOT called, LLM is used directly."""
        parser = PriceParser()

        mock_api_extractor = AsyncMock()
        mock_api_extractor.extract = AsyncMock(return_value=None)
        parser._api_extractors["willys"] = mock_api_extractor

        llm_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.90,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm_result

            result = await parser.extract_price(
                text_content="page content",
                store_slug="willys",
                product_name="Mjolk",
                # No store_url provided
            )

        mock_api_extractor.extract.assert_not_called()
        mock_llm.assert_called_once()
        assert result == llm_result

    @pytest.mark.asyncio
    async def test_extract_price_uses_jsonld_before_llm(self) -> None:
        """html_content with a JSON-LD Product short-circuits the LLM cascade."""
        parser = PriceParser()
        html = (
            '<script type="application/ld+json">{"@type":"Product","name":"Mjolk",'
            '"offers":{"@type":"Offer","price":"18.50","priceCurrency":"SEK"}}</script>'
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            result = await parser.extract_price(
                text_content="page content",
                store_slug="apotea",
                product_name="Mjolk",
                html_content=html,
            )

        mock_llm.assert_not_called()
        assert result.price_sek == Decimal("18.50")
        assert result.raw_response["source"] == "jsonld"

    @pytest.mark.asyncio
    async def test_extract_price_falls_back_to_llm_without_jsonld(self) -> None:
        """html_content without JSON-LD falls through to the LLM cascade."""
        parser = PriceParser()

        llm_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.9,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm_result
            result = await parser.extract_price(
                text_content="page content",
                store_slug="apotea",
                html_content="<html><body>no structured data</body></html>",
            )

        mock_llm.assert_called_once()
        assert result == llm_result

    @pytest.mark.asyncio
    async def test_extract_price_discards_below_acceptance_floor(self) -> None:
        """All models under MIN_ACCEPT_CONFIDENCE -> price is discarded, not returned."""
        parser = PriceParser()

        low_result = PriceExtractionResult(
            price_sek=Decimal("99.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.2,  # below both model thresholds and the 0.6 floor
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = low_result
            result = await parser.extract_price("page content", "apotea")

        # Both cascade models were tried
        assert mock_llm.call_count == 2
        # Price is discarded so callers skip recording
        assert result.price_sek is None
        assert result.confidence == 0.2
        assert result.raw_response["source"] == "discarded_low_confidence"

    @pytest.mark.asyncio
    async def test_extract_price_accepts_above_floor_below_model_threshold(self) -> None:
        """Confidence between the floor and model thresholds is still accepted."""
        parser = PriceParser()

        mid_result = PriceExtractionResult(
            price_sek=Decimal("49.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.65,  # above 0.6 floor, below scout's 0.70
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mid_result
            result = await parser.extract_price("page content", "apotea")

        # scout rejects (0.65 < 0.70), haiku accepts (0.65 >= 0.6 floor)
        assert result.price_sek == Decimal("49.90")
