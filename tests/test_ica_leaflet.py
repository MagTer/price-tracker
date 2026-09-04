"""Tests for the ICA veckoblad cross-check — parse, join, fetch-and-cache, and stamp.

The fixtures mirror the live shape read 2026-09-04 from
``www.ica.se/erbjudanden/maxi-ica-stormarknad-sandviken-1003396/`` and its Björksätra
sibling: ``window.__INITIAL_DATA__`` is a JS object literal (not JSON — it carries
``undefined`` and ``new Map([])``) whose ``offers.weeklyOffers[]`` holds one entry per
advertised offer, with ``storeInd``/``onlineInd`` per butik.

The join under test: a promotion's ``retailerPromotionId`` first segment IS the leaflet
offer's ``id`` (5004133591-1787037115 -> 5004133591, the Kungsörnen makaroner row).
"""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.ica_leaflet import (
    annotate_extraction,
    leaflet_url_for,
    load_leaflet_urls,
    parse_leaflet,
    promotion_leaflet_id,
)
from domain.result import (
    PriceExtractionResult,
    offer_channel_note,
    offer_in_leaflet,
)
from infra.ica_leaflet import IcaLeafletCache

MAXI_URL = "https://handlaprivatkund.ica.se/stores/1003396/products/nagot/2154048"


def _offer(
    offer_id: str = "5004133591",
    name: str = "Makaroner, Svenska pastaformer",
    mechanic: str = "5 för 50 kr",
    valid_to: str | None = "2026-09-06T00:00:00",
    store_ind: bool = True,
    online_ind: bool = True,
) -> dict:
    return {
        "id": offer_id,
        "details": {
            "brand": "Kungsörnen",
            "name": name,
            "mechanicInfo": mechanic,
            "packageInformation": "450-750 g",
            "isSelfScan": False,
        },
        "usesLeft": None,
        "validTo": valid_to,
        "stores": [
            {
                "storeMarketingName": "Maxi ICA Stormarknad Sandviken",
                "BMSStoreId": 11068,
                "regularPrice": "12,44-17,88",
                "ecomDisclaimer": "",
                "onlineInd": online_ind,
                "storeInd": store_ind,
            }
        ],
        "traits": ["Butiksunikt"],
    }


def _page(offers: list[dict], *, js_literals: bool = True) -> str:
    """The erbjudandesida, with the JS-only values the live page really carries."""
    payload = json.dumps({"offers": {"weeklyOffers": offers}}, ensure_ascii=False)
    if js_literals:
        # Exactly the two shapes the live page uses, in places the decoder must survive.
        payload = payload.replace('"usesLeft": null', '"usesLeft": undefined').replace(
            '"weeklyOffers"', '"products": {"values": new Map([])}, "weeklyOffers"'
        )
    return f"<html><body><script>window.__INITIAL_DATA__ = {payload};</script></body></html>"


class TestParseLeaflet:
    def test_reads_the_offers_through_the_js_only_literals(self):
        offers = parse_leaflet(_page([_offer(), _offer(offer_id="2005267735")]))
        assert offers is not None
        assert set(offers) == {"5004133591", "2005267735"}
        row = offers["5004133591"]
        assert row.mechanic == "5 för 50 kr"
        assert row.valid_to == date(2026, 9, 6)
        assert row.store_ind is True

    def test_undefined_inside_a_string_value_is_left_alone(self):
        """The patch runs at the decoder's own failure position, never as a regex.

        A sweeping `s.replace("undefined", "null")` would corrupt any offer whose text
        contains the word — a product name, a disclaimer — and the corruption would show
        up as a wrong name on a row, not as an error.
        """
        offers = parse_leaflet(_page([_offer(name="Sås undefined smak")]))
        assert offers is not None
        assert offers["5004133591"].name == "Sås undefined smak"

    def test_an_empty_leaflet_is_unknown_not_an_empty_answer(self):
        """A butik always advertises something (187 and 48 when this was measured).

        Returning {} here would mark every one of that butik's campaigns as absent from a
        veckoblad we never actually read — a confident wrong answer written into history.
        """
        assert parse_leaflet(_page([])) is None

    def test_a_page_without_the_data_blob_is_unknown(self):
        assert parse_leaflet("<html><body>bara en sida</body></html>") is None

    def test_an_unknown_js_literal_ends_as_unknown(self):
        html = '<html><script>window.__INITIAL_DATA__ = {"offers": new Set([1])};</script></html>'
        assert parse_leaflet(html) is None

    def test_offers_without_a_usable_id_are_skipped_not_guessed(self):
        offers = parse_leaflet(_page([_offer(), {"details": {"name": "namnlös"}}]))
        assert offers is not None
        assert set(offers) == {"5004133591"}


class TestPromotionJoin:
    def test_the_first_segment_is_the_leaflet_id(self):
        assert promotion_leaflet_id("5004133591-1787037115") == "5004133591"

    def test_a_value_that_is_not_two_numeric_segments_is_unknown(self):
        # Half an id must not be judged: no verdict beats a wrong one.
        assert promotion_leaflet_id("") is None
        assert promotion_leaflet_id(None) is None
        assert promotion_leaflet_id(12345) is None
        assert promotion_leaflet_id("-1787037115") is None
        assert promotion_leaflet_id("abc-123") is None


class TestLeafletConfig:
    def test_the_operators_butiker_are_the_built_in_default(self):
        assert leaflet_url_for(MAXI_URL) is not None

    def test_a_butik_with_no_configured_leaflet_is_unknown(self):
        unknown = "https://handlaprivatkund.ica.se/stores/9999999/products/x/1"
        assert leaflet_url_for(unknown) is None
        assert leaflet_url_for("https://www.willys.se/produkt/nagot-123_ST") is None

    def test_a_url_outside_ica_is_dropped_rather_than_fetched(self):
        """This table feeds a scheduled outgoing request, so a typo fails closed."""
        urls = load_leaflet_urls('{"1003396": "https://example.com/erbjudanden/"}')
        assert "1003396" not in urls

    def test_malformed_json_falls_back_to_the_defaults(self):
        assert load_leaflet_urls("{not json") == load_leaflet_urls(None)


def _extraction(**overrides) -> PriceExtractionResult:
    raw = {
        "source": "ica_page",
        "offer_retailer_promotion_id": "5004133591-1787037115",
    }
    raw.update(overrides.pop("raw", {}))
    fields = {
        "price_sek": Decimal("12.44"),
        "store_unit_price_sek": None,
        "offer_price_sek": Decimal("10.00"),
        "offer_type": "kampanj",
        "offer_details": "5 för 50 kr",
        "in_stock": True,
        "confidence": 0.98,
        "pack_size": None,
        "package_amount": None,
        "package_unit": None,
        "raw_response": raw,
    }
    fields.update(overrides)
    return PriceExtractionResult(**fields)


def _lookup(offers) -> MagicMock:
    lookup = MagicMock()
    lookup.offers_for = AsyncMock(return_value=offers)
    return lookup


class TestAnnotateExtraction:
    @pytest.mark.asyncio
    async def test_an_advertised_campaign_is_recorded_with_the_butiks_own_date(self):
        extraction = _extraction()
        await annotate_extraction(extraction, MAXI_URL, _lookup(parse_leaflet(_page([_offer()]))))
        assert extraction.raw_response["offer_in_leaflet"] is True
        assert extraction.raw_response["offer_leaflet_valid_to"] == "2026-09-06"
        note = offer_channel_note(extraction.raw_response)
        assert note == "finns i butikens veckoblad t.o.m. 6/9"

    @pytest.mark.asyncio
    async def test_a_campaign_absent_from_the_leaflet_is_recorded_as_absent(self):
        """The rapsolja case: DEFAULT presentation, and not advertised at all."""
        extraction = _extraction()
        await annotate_extraction(
            extraction, MAXI_URL, _lookup(parse_leaflet(_page([_offer(offer_id="2005267735")])))
        )
        assert extraction.raw_response["offer_in_leaflet"] is False
        assert offer_channel_note(extraction.raw_response) == (
            "finns inte i butikens veckoblad — kan gälla endast e-handeln"
        )

    @pytest.mark.asyncio
    async def test_the_butiks_own_store_flag_outranks_our_inference(self):
        extraction = _extraction()
        await annotate_extraction(
            extraction, MAXI_URL, _lookup(parse_leaflet(_page([_offer(store_ind=False)])))
        )
        assert offer_channel_note(extraction.raw_response) == (
            "butiken anger att erbjudandet inte gäller i butiken"
        )

    @pytest.mark.asyncio
    async def test_an_unknown_leaflet_records_no_verdict_at_all(self):
        """The single most important guard: unknown must never render as "not advertised"."""
        extraction = _extraction()
        await annotate_extraction(extraction, MAXI_URL, _lookup(None))
        assert "offer_in_leaflet" not in extraction.raw_response
        assert offer_in_leaflet(extraction.raw_response) is None

    @pytest.mark.asyncio
    async def test_a_raising_lookup_leaves_the_extraction_untouched(self):
        """A note on a price check may never take the price check down with it."""
        lookup = MagicMock()
        lookup.offers_for = AsyncMock(side_effect=RuntimeError("leaflet exploded"))
        extraction = _extraction()
        await annotate_extraction(extraction, MAXI_URL, lookup)
        assert "offer_in_leaflet" not in extraction.raw_response

    @pytest.mark.asyncio
    async def test_a_point_with_no_offer_is_not_cross_checked(self):
        extraction = _extraction(offer_price_sek=None)
        lookup = _lookup(parse_leaflet(_page([_offer()])))
        await annotate_extraction(extraction, MAXI_URL, lookup)
        assert "offer_in_leaflet" not in extraction.raw_response
        lookup.offers_for.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_store_that_records_no_promotion_id_is_not_cross_checked(self):
        """Every store but ICA — no id, no join, no verdict, and no fetch."""
        extraction = _extraction(raw={"offer_retailer_promotion_id": None})
        lookup = _lookup(parse_leaflet(_page([_offer()])))
        await annotate_extraction(extraction, "https://www.willys.se/produkt/x-1_ST", lookup)
        assert "offer_in_leaflet" not in extraction.raw_response
        lookup.offers_for.assert_not_awaited()


class TestLeafletCache:
    """The fetching half: politeness, the breaker, and one fetch per butik per day."""

    @staticmethod
    def _cache(fetch_result) -> tuple[IcaLeafletCache, MagicMock, MagicMock, MagicMock]:
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value=fetch_result)
        limiter = MagicMock()
        limiter.acquire = AsyncMock(return_value=0.0)
        blocks = MagicMock()
        blocks.blocked_until = MagicMock(return_value=None)
        return IcaLeafletCache(fetcher, limiter, blocks), fetcher, blocks, limiter

    @pytest.mark.asyncio
    async def test_a_good_page_is_parsed_and_the_slot_is_spent(self):
        cache, _fetcher, blocks, limiter = self._cache(
            {"ok": True, "html": _page([_offer()]), "blocked": False}
        )
        offers = await cache.offers_for(MAXI_URL)
        assert offers is not None and "5004133591" in offers
        # The politeness ledger is spent BEFORE the request, keyed on the leaflet's own
        # host — www.ica.se is not the host the products come from.
        limiter.acquire.assert_awaited()
        blocks.record_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_the_leaflet_is_fetched_once_per_butik_per_day(self):
        """44 due ICA links in one cycle must cost ONE request to www.ica.se."""
        cache, fetcher, _blocks, _limiter = self._cache(
            {"ok": True, "html": _page([_offer()]), "blocked": False}
        )
        for _ in range(5):
            assert await cache.offers_for(MAXI_URL) is not None
        assert fetcher.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_a_bot_wall_trips_the_breaker_and_answers_unknown(self):
        cache, _fetcher, blocks, _limiter = self._cache(
            {"ok": False, "html": "", "blocked": True, "error": "202"}
        )
        assert await cache.offers_for(MAXI_URL) is None
        blocks.record_block.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_cooling_store_is_not_fetched_at_all(self):
        cache, fetcher, blocks, _limiter = self._cache({"ok": True, "html": _page([_offer()])})
        blocks.blocked_until = MagicMock(return_value="soon")
        assert await cache.offers_for(MAXI_URL) is None
        fetcher.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_raising_fetcher_answers_unknown_rather_than_raising(self):
        cache, fetcher, _blocks, _limiter = self._cache({})
        fetcher.fetch = AsyncMock(side_effect=RuntimeError("network gone"))
        assert await cache.offers_for(MAXI_URL) is None

    @pytest.mark.asyncio
    async def test_a_butik_with_no_configured_leaflet_never_fetches(self):
        cache, fetcher, _blocks, _limiter = self._cache({"ok": True, "html": _page([_offer()])})
        assert await cache.offers_for("https://www.willys.se/produkt/x-1_ST") is None
        fetcher.fetch.assert_not_awaited()
