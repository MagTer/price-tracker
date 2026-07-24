"""Tests for price parser module."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.parser import (
    PriceExtractionResult,
    PriceParser,
    _build_metadata_context,
    _to_decimal,
    _to_int,
)

_SPA_HTML = """<!doctype html><html><head>
<title>Falukorv Klassikern 800g Scan &ndash; online</title>
<meta name="description" content="Falukorv Klassikern 800g Scan &amp; hemleverans">
<meta property="og:title" content="Falukorv Klassikern 800g Scan">
<script type="application/ld+json">{"@type":"Product","name":"Falukorv"}</script>
</head><body><div id="root"></div></body></html>"""


class TestMetadataContext:
    """_build_metadata_context: give the LLM the head/JSON-LD a SPA hides from visible text."""

    def test_leads_with_title_meta_and_jsonld(self) -> None:
        ctx = _build_metadata_context(_SPA_HTML, "sparse visible text")
        assert "Title: Falukorv Klassikern 800g Scan" in ctx  # entities unescaped
        assert "description: Falukorv Klassikern 800g Scan & hemleverans" in ctx
        assert "og:title: Falukorv Klassikern 800g Scan" in ctx
        assert '"@type":"Product"' in ctx  # raw JSON-LD carried through
        assert "Visible text:\nsparse visible text" in ctx
        # Head signals come before the visible text.
        assert ctx.index("Title:") < ctx.index("Visible text:")

    def test_no_signals_falls_back_to_visible_text(self) -> None:
        """A blocked/empty page (no head signals) must not regress below text-only."""
        assert _build_metadata_context("<html><body></body></html>", "just text") == "just text"
        assert _build_metadata_context("", "just text") == "just text"

    def test_truncates_to_budget(self) -> None:
        big_html = f"<title>{'x' * 100}</title>"
        ctx = _build_metadata_context(big_html, "y" * 10000, budget=200)
        assert len(ctx) == 200


class TestNumericCoercion:
    """_to_decimal / _to_int: an LLM's price/amount survives whatever shape it returns.

    A bare Decimal(str(...)) on "32,90 kr" raised decimal.ConversionSyntax — the failure
    that killed the old llama price path and would bite any model (deepseek returns the
    same comma+currency shape). These pin the normalization contract.
    """

    @pytest.mark.parametrize(
        "value,expected",
        [
            (29.90, Decimal("29.90")),
            (30, Decimal("30")),
            ("29.90", Decimal("29.90")),
            ("32,90", Decimal("32.90")),  # Swedish decimal comma
            ("32,90 kr", Decimal("32.90")),  # comma + currency suffix
            ("29.90 SEK", Decimal("29.90")),
            ("0,5 l", Decimal("0.5")),  # amount with unit
            ("1 234,50", Decimal("1234.50")),  # space thousands + comma decimal
            ("1 234,50 kr", Decimal("1234.50")),  # NBSP thousands separator
            ("1,234.50", Decimal("1234.50")),  # US style: comma thousands, dot decimal
            (Decimal("12.34"), Decimal("12.34")),
        ],
    )
    def test_to_decimal_normalizes(self, value, expected) -> None:
        assert _to_decimal(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "kr", "n/a", "på lager", True, False])
    def test_to_decimal_returns_none_for_non_numbers(self, value) -> None:
        assert _to_decimal(value) is None

    @pytest.mark.parametrize(
        "value,expected",
        [(24, 24), ("24", 24), ("24-pack", 24), ("24 st", 24), (None, None), ("none", None)],
    )
    def test_to_int_extracts_pack_size(self, value, expected) -> None:
        assert _to_int(value) == expected


class TestPriceExtractionResult:
    """Tests for PriceExtractionResult dataclass."""

    def test_create_with_all_fields(self) -> None:
        """Test creating result with all fields populated."""
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

    def test_create_with_none_values(self) -> None:
        """Test creating result with None values."""
        result = PriceExtractionResult(
            price_sek=None,
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=False,
            confidence=0.3,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={},
        )

        assert result.price_sek is None
        assert result.store_unit_price_sek is None
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None
        assert result.in_stock is False
        assert result.confidence == 0.3
        assert result.pack_size is None
        assert result.package_amount is None
        assert result.package_unit is None


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
            assert result.store_unit_price_sek == Decimal("149.50")
            assert result.offer_price_sek is None
            assert result.in_stock is True
            assert result.confidence == 0.95
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_model_survives_comma_and_currency_price(self) -> None:
        """A model that returns "32,90 kr" must not raise — the old bare Decimal() did.

        This is the regression guard for the decimal.ConversionSyntax failure. deepseek
        (and llama) return Swedish comma + "kr" as readily as a bare number.
        """
        parser = PriceParser()

        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "price": "32,90 kr",
                                "unit_price": "41,13 kr/kg",
                                "offer_price": "29,90",
                                "package_amount": "0,8",
                                "pack_size": "24-pack",
                                "in_stock": True,
                                "confidence": 0.9,
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

            result = await parser._extract_with_model("test prompt", "ica")

            assert result.price_sek == Decimal("32.90")
            assert result.store_unit_price_sek == Decimal("41.13")
            assert result.offer_price_sek == Decimal("29.90")
            assert result.package_amount == Decimal("0.8")
            assert result.pack_size == 24

    @pytest.mark.asyncio
    async def test_no_printed_unit_price_is_not_synthesized(self) -> None:
        """A page that prints no unit price yields None — never price / pack_size (D-05).

        Regression guard: store_unit_price_sek is "what the store claims". If the decoder
        ever computes a fallback again, the "Store says" column silently becomes a mix of
        store claims and our own arithmetic, and a store/page mismatch stops being visible.
        """
        parser = PriceParser()

        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "price": 139.90,
                                # no "unit_price" — the page printed none
                                "pack_size": 24,
                                "in_stock": True,
                                "confidence": 0.9,
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

        assert result.price_sek == Decimal("139.90")
        assert result.pack_size == 24
        # 139.90 / 24 == 5.829… would be the old synthesized value. It must NOT appear.
        assert result.store_unit_price_sek is None

    @pytest.mark.asyncio
    async def test_parses_package_amount_and_unit(self) -> None:
        """package_amount decodes to Decimal and package_unit to a lowercased str (D-08)."""
        parser = PriceParser()

        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "price": 24.90,
                                "package_amount": 0.5,
                                "package_unit": "L",
                                "in_stock": True,
                                "confidence": 0.9,
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

        assert isinstance(result.package_amount, Decimal)
        assert result.package_amount == Decimal("0.5")
        assert result.package_unit == "l"

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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.85,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_result

            result = await parser.extract_price("page content", "ica-maxi", "Mjölk")

            # Should call with the primary model first
            mock_extract.assert_called_once()
            assert "deepseek/deepseek-v4-flash" in str(mock_extract.call_args)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_extract_price_retries_with_fallback_on_low_confidence(self) -> None:
        """Test that extract_price retries with fallback if primary confidence is low."""
        parser = PriceParser()

        primary_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.5,  # Below threshold
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={},
        )

        fallback_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.95,  # High confidence
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.9,
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.99,
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.85,
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.90,
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.9,
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.2,  # below both model thresholds and the 0.6 floor
            pack_size=None,
            package_amount=None,
            package_unit=None,
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
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.65,  # above 0.6 floor, below scout's 0.70
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mid_result
            result = await parser.extract_price("page content", "apotea")

        # scout rejects (0.65 < 0.70), haiku accepts (0.65 >= 0.6 floor)
        assert result.price_sek == Decimal("49.90")


def _jsonld_base(
    price: str = "108.95",
    store_unit_price_sek: Decimal | None = None,
    offer_price_sek: Decimal | None = None,
) -> PriceExtractionResult:
    """A JSON-LD extraction as the extractor emits it: price + stock, nothing else."""
    return PriceExtractionResult(
        price_sek=Decimal(price),
        store_unit_price_sek=store_unit_price_sek,
        offer_price_sek=offer_price_sek,
        offer_type=None,
        offer_details=None,
        in_stock=True,
        confidence=0.95,
        pack_size=None,
        package_amount=None,
        package_unit=None,
        raw_response={"source": "jsonld", "price": float(price), "in_stock": True},
    )


def _llm_result(
    price: str | None = "99.90",
    confidence: float = 0.9,
    in_stock: bool = False,
    **fields,
) -> PriceExtractionResult:
    defaults: dict = {
        "store_unit_price_sek": None,
        "offer_price_sek": None,
        "offer_type": None,
        "offer_details": None,
        "pack_size": None,
        "package_amount": None,
        "package_unit": None,
    }
    defaults.update(fields)
    return PriceExtractionResult(
        price_sek=Decimal(price) if price is not None else None,
        in_stock=in_stock,
        confidence=confidence,
        raw_response={"source": "llm"},
        **defaults,
    )


class TestEnrichWithLlm:
    """enrich_with_llm: JSON-LD stays the price authority, the LLM only fills gaps."""

    @pytest.mark.asyncio
    async def test_fills_only_none_fields_and_base_wins_on_price(self) -> None:
        """The LLM disagrees on price and stock — base wins; it contributes only gaps."""
        parser = PriceParser()
        base = _jsonld_base(price="108.95")
        llm = _llm_result(
            price="99.90",  # disagrees with base — must be ignored
            in_stock=False,  # disagrees with base — must be ignored
            confidence=0.9,
            store_unit_price_sek=Decimal("6.81"),
            offer_price_sek=Decimal("89.90"),
            offer_type="kampanj",
            offer_details="Kop 2 betala for 1",
            pack_size=16,
            package_amount=Decimal("16"),
            package_unit="st",
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm
            enriched = await parser.enrich_with_llm(
                base, text_content="page", store_slug="ica", product_name="Lambi"
            )

        assert enriched.price_sek == Decimal("108.95")  # base authority
        assert enriched.in_stock is True  # base authority
        assert enriched.confidence == 0.95  # base authority
        assert enriched.store_unit_price_sek == Decimal("6.81")
        assert enriched.offer_price_sek == Decimal("89.90")
        assert enriched.offer_type == "kampanj"
        assert enriched.offer_details == "Kop 2 betala for 1"
        assert enriched.pack_size == 16
        assert enriched.package_amount == Decimal("16")
        assert enriched.package_unit == "st"
        # base is NOT mutated
        assert base.offer_price_sek is None
        assert base.raw_response.get("enriched") is None

    @pytest.mark.asyncio
    async def test_base_fields_survive_when_already_set(self) -> None:
        """A field base already carries is never overwritten by the LLM's value."""
        parser = PriceParser()
        base = _jsonld_base(store_unit_price_sek=Decimal("5.83"))
        llm = _llm_result(confidence=0.9, store_unit_price_sek=Decimal("99.00"))

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm
            enriched = await parser.enrich_with_llm(
                base, text_content="page", store_slug="ica", product_name="Lambi"
            )

        assert enriched.store_unit_price_sek == Decimal("5.83")

    @pytest.mark.asyncio
    async def test_below_floor_returns_base_unchanged(self) -> None:
        parser = PriceParser()
        base = _jsonld_base()
        llm = _llm_result(confidence=0.2, pack_size=16)

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm
            enriched = await parser.enrich_with_llm(
                base, text_content="page", store_slug="ica", product_name="Lambi"
            )

        assert enriched is base

    @pytest.mark.asyncio
    async def test_cascade_exception_returns_base_without_raising(self) -> None:
        parser = PriceParser()
        base = _jsonld_base()

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("all models down")
            enriched = await parser.enrich_with_llm(
                base, text_content="page", store_slug="ica", product_name="Lambi"
            )

        assert enriched is base

    @pytest.mark.asyncio
    async def test_raw_response_keeps_jsonld_source_and_marks_enrichment(self) -> None:
        """Scheduler stats key on raw_response['source'] — enrichment must not change it."""
        parser = PriceParser()
        base = _jsonld_base()
        llm = _llm_result(confidence=0.85, pack_size=16)

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm
            enriched = await parser.enrich_with_llm(
                base, text_content="page", store_slug="ica", product_name="Lambi"
            )

        assert enriched.raw_response["source"] == "jsonld"
        assert enriched.raw_response["enriched"] is True
        assert enriched.raw_response["enrichment_confidence"] == 0.85


class TestExtractProductMetadataHtml:
    """extract_product_metadata feeds the SPA head/JSON-LD signals to the model (D)."""

    @pytest.mark.asyncio
    async def test_html_signals_reach_the_prompt(self) -> None:
        parser = PriceParser()
        captured: dict[str, str] = {}

        async def fake_call(prompt: str, model: str) -> dict:
            captured["prompt"] = prompt
            return {"name": "Falukorv Klassikern", "brand": "Scan", "confidence": 0.95}

        with patch.object(parser, "_call_model_json", side_effect=fake_call):
            meta = await parser.extract_product_metadata(
                "sparse visible text", "ica", html_content=_SPA_HTML
            )

        # The prompt carried the title/JSON-LD the stripped text lacked.
        assert "Falukorv Klassikern 800g Scan" in captured["prompt"]
        assert '"@type":"Product"' in captured["prompt"]
        assert meta is not None
        assert meta.name == "Falukorv Klassikern"
        assert meta.brand == "Scan"

    @pytest.mark.asyncio
    async def test_without_html_uses_visible_text_only(self) -> None:
        parser = PriceParser()
        captured: dict[str, str] = {}

        async def fake_call(prompt: str, model: str) -> dict:
            captured["prompt"] = prompt
            return {"name": "X", "confidence": 0.9}

        with patch.object(parser, "_call_model_json", side_effect=fake_call):
            await parser.extract_product_metadata("only visible text", "med24")

        assert "only visible text" in captured["prompt"]
        assert "JSON-LD" not in captured["prompt"]
