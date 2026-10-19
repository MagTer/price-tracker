"""The extraction ladder ends at the structured tiers — no language model ever prices a check.

Decided 2026-10-19 on prod evidence: across the tracker's whole history the LLM fallback
answered 8 checks, all of them on two links whose product no longer existed at the store
(a Willys code the API 404'd, a JYSK article gone from its ProductGroup), and all 8 points
were wrong — an inverted ordinarie, an EU-Omnibus disclosure stored as villkor, and a
49,90 kr / 89,90 kr/kg that appears nowhere on the page. Because the cascade always answered,
those checks reported "ok", so link_health never flagged the links either. These tests pin
the replacement: a structured refusal ends the ladder with a REASON, and a page no
structured tier can read is an honest no_price.
"""

import dataclasses
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.extractors.willys_api import WillysApiExtractor
from domain.parser import PriceParser
from domain.result import ProductGoneError, extraction_source
from domain.service import perform_price_check
from tests.test_check_log import _fixtures, _RecordingLog
from tests.test_jysk_extractor import NORA_URL, _group

_WILLYS_URL = "https://www.willys.se/produkt/Mellanrost-Classic-Bryggkaffe-100002676_ST"

# A JSON-LD Product that is NOT the product the link names — what a fall-through would
# happily record if the ladder kept going after a store said "that product is gone".
_FOREIGN_JSONLD = (
    '<script type="application/ld+json">{"@type":"Product","name":"Något annat",'
    '"offers":{"@type":"Offer","price":"49.90","priceCurrency":"SEK"}}</script>'
)


def _no_network():
    """Any outgoing model call fails the test loudly instead of being swallowed."""
    return patch("httpx.AsyncClient.post", side_effect=AssertionError("a model was called"))


class TestNothingStructuredMeansNoPrice:
    @pytest.mark.asyncio
    async def test_a_page_with_no_structured_price_is_no_price_not_a_model_call(self) -> None:
        parser = PriceParser()
        with _no_network():
            result = await parser.extract_price(
                text_content="Bryggkaffe 49,90 kr Jämförpris 89,90 kr/kg",
                store_slug="apotea",
                product_name="Bryggkaffe",
                store_url="https://www.apotea.se/x",
                html_content="<html><body>Bryggkaffe 49,90 kr</body></html>",
            )

        assert result.price_sek is None
        assert extraction_source(result) == "none"
        assert result.raw_response.get("reason")


class TestAStoreSayingTheProductIsGoneEndsTheLadder:
    @pytest.mark.asyncio
    async def test_willys_api_404_is_product_gone(self) -> None:
        fetcher = MagicMock()
        fetcher.fetch_json = AsyncMock(
            return_value={"ok": False, "data": None, "error": "HTTP 404", "blocked": False}
        )
        limiter = MagicMock()
        limiter.acquire = AsyncMock()
        with (
            patch("infra.providers.get_fetcher", return_value=fetcher),
            patch("infra.providers.get_rate_limiter", return_value=limiter),
            pytest.raises(ProductGoneError, match="HTTP 404"),
        ):
            await WillysApiExtractor().extract(_WILLYS_URL)

    @pytest.mark.asyncio
    async def test_a_gone_product_is_not_answered_by_a_lower_tier(self) -> None:
        parser = PriceParser()
        api = MagicMock()
        api.extract = AsyncMock(side_effect=ProductGoneError("Willys API: HTTP 404"))
        parser._api_extractors["willys"] = api

        with _no_network():
            result = await parser.extract_price(
                text_content="page",
                store_slug="willys",
                product_name="Bryggkaffe",
                store_url=_WILLYS_URL,
                html_content=_FOREIGN_JSONLD,
            )

        assert result.price_sek is None
        assert extraction_source(result) == "product_gone"
        assert "finns inte" in str(result.raw_response["reason"])
        assert "HTTP 404" in str(result.raw_response["reason"])

    @pytest.mark.asyncio
    async def test_jysk_article_missing_from_its_group_is_product_gone(self) -> None:
        parser = PriceParser()
        # The group on the page no longer carries the article the URL names.
        html = _group(article="2349551") + _FOREIGN_JSONLD

        with _no_network():
            result = await parser.extract_price(
                text_content="page",
                store_slug="jysk",
                product_name="Badlakan NORA 100x150 sand",
                store_url=NORA_URL,
                html_content=html,
            )

        assert result.price_sek is None
        assert extraction_source(result) == "product_gone"
        assert "2342607" in str(result.raw_response["reason"])


class TestTheReasonReachesCheckAttempts:
    @pytest.mark.asyncio
    async def test_no_price_records_why(self) -> None:
        """detail is what link_health hands Fel & luckor; an empty one says nothing."""
        ps, product, store, session, fetcher, parser = _fixtures()
        parser.extract_price.return_value = PriceParser._no_price(
            "product_gone", "produkten finns inte hos butiken (Willys API: HTTP 404)"
        )
        log = _RecordingLog()

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=log,
            attempt_source="scheduler",
        )

        assert outcome.failure_reason == "no_price"
        assert log.calls[0]["outcome"] == "no_price"
        assert log.calls[0]["extraction_source"] == "product_gone"
        assert "HTTP 404" in log.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_a_jsonld_price_is_recorded_without_a_model_pass(self) -> None:
        """Enrichment is gone too: its offers turned out to be JSON-LD's own ("Spara 39 kr"
        is the jsonld extractor's format), leaving a jämförpris on a handful of points as its
        only contribution — not worth a model reading the page beside a structured price."""
        ps, product, store, session, fetcher, parser = _fixtures()
        parser.extract_price.return_value = dataclasses.replace(
            PriceParser._no_price("none", "x"),
            price_sek=Decimal("129.00"),
            raw_response={"source": "jsonld"},
        )

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=None,
        )

        assert outcome.success is True
        assert outcome.extraction.price_sek == Decimal("129.00")
        assert not hasattr(parser, "enrich_with_llm") or not parser.enrich_with_llm.called
