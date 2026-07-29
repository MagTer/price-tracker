"""Tests for LykoExtractor — the window.CURRENT_PAGE page-state tier for Lyko.

The fixtures mirror the live shapes verified 2026-07-29: Palmolive Hand Wash Refill
Milk & Honey 500 ml (no campaign, JSON-LD price 33) and N.C.P. Hand Wash 201 Apple &
Driftwood 300 ml (80 % off — JSON-LD publishes only the 58 kr campaign price while the
ordinarie 290 exists solely in the state). The extractor's job is to keep a Lyko
campaign honest under the ORDINARIE + OFFER model, and to refuse the two comparison
prices that are NOT a campaign: "Rek. pris" (the manufacturer's RRP) and "Utan
paketpris" (a gift set's parts bought separately), both of which were observed live on
products Lyko itself rendered as undiscounted.
"""

import json
from decimal import Decimal

from domain.extractors.lyko import LykoExtractor

URL = "https://lyko.com/sv/palmolive/palmolive-hand-wash-refill-milk---honey-500-ml"
SLUG = "palmolive-hand-wash-refill-milk---honey-500-ml"

# A decoy that precedes CURRENT_PAGE in every live page: the app shell carries no product
# data, and an extractor that grabs "the first window.* JSON" would read it by mistake.
_APP_SHELL = 'window.APP_SHELL_DATA = {"siteSettings": {"culture": "sv-SE"}, "cart": {}};'


def _price(current: float, comparisons: list[dict] | None = None, **overrides) -> dict:
    """A price object in the live CURRENT_PAGE shape."""
    price = {
        "current": current,
        "comparisonPrices": comparisons or [],
        "currency": "SEK",
        "useDiscountStyling": bool(comparisons),
        "discountPercent": None,
        "isMemberPrice": False,
        "isComboDealPrice": False,
        "campaignPriceValidUntil": None,
        "memberPrice": None,
        "memberPriceValidUntil": None,
        "cannotBeCombinedWithCoupons": False,
        "vatRate": 25.0,
        # Null on every product observed live — Lyko prints no jämförpris at all.
        "unitPrice": None,
    }
    price.update(overrides)
    return price


def _comparison(type_: str, value: float) -> dict:
    text = {
        "ordinary": "Tid. pris",
        "withoutCampaign": "Utan kampanj",
        "recommended": "Rek. pris",
        "packageContentSum": "Utan paketpris:",
    }
    return {"type": type_, "typeText": text.get(type_, type_), "value": value}


def _page(**overrides) -> dict:
    page = {
        "canonicalUrl": "/sv/palmolive/palmolive-hand-wash-refill-milk---honey-500-ml",
        "url": "/sv/palmolive/palmolive-hand-wash-refill-milk---honey-500-ml",
        "code": "3393-105-0500",
        "displayName": "Palmolive Hand Wash Refill Milk & Honey 500 ml",
        "entryName": "Hand Wash Refill Milk & Honey",
        "brandName": "Palmolive",
        "size": 500.0,
        "unit": "ml",
        "sizeAndUnit": "500 ml",
        "price": _price(33.0),
        "onlineStatus": {"interactionType": "buy", "isGwpGift": False, "text": None},
        # The physical shops, NOT the webshop: live pages carry "outOfStockInAllStores"
        # on products that are in stock and on sale online.
        "variantStoreAvailabilityStatus": "outOfStockInAllStores",
        "linkingData": {
            "name": "Palmolive Hand Wash Refill Milk & Honey 500 ml",
            "offers": {
                "priceCurrency": "SEK",
                "price": "33",
                "availability": "http://schema.org/InStock",
                "@type": "Offer",
            },
            "@type": "Product",
        },
        "variants": [],
    }
    page.update(overrides)
    return page


def _html(page: dict) -> str:
    return (
        "<html><head></head><body><div>sida</div>"
        f"<script> window.CURRENT_VERSION = '1.0.0'; {_APP_SHELL} "
        f"window.CURRENT_PAGE = {json.dumps(page, ensure_ascii=False)};</script>"
        "</body></html>"
    )


class TestCampaignPricing:
    def test_a_campaign_records_ordinarie_as_price_and_current_as_offer(self) -> None:
        """The reason this extractor exists: the JSON-LD publishes only 58, while the
        state knows the ordinarie is 290 — price_sek must be the ordinarie."""
        page = _page(
            price=_price(58.0, [_comparison("ordinary", 290.0)], discountPercent=80),
        )
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("290")
        assert result.offer_price_sek == Decimal("58")
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 232 kr"
        assert result.confidence == LykoExtractor.CONFIDENCE
        assert result.raw_response["source"] == "lyko_page"
        assert result.raw_response["ordinarie_type"] == "ordinary"
        assert result.raw_response["discount_percent"] == 80.0

    def test_without_campaign_is_also_an_ordinarie(self) -> None:
        """ "Utan kampanj" is the price with the campaign removed — an ordinarie by
        another name, observed live on HICKAP (139.00 against a current 104.25)."""
        page = _page(price=_price(104.25, [_comparison("withoutCampaign", 139.0)]))
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("139")
        assert result.offer_price_sek == Decimal("104.25")

    def test_recommended_price_is_not_a_campaign(self) -> None:
        """ "Rek. pris" is the MANUFACTURER's price, not one Lyko charged. Live evidence:
        Maria Åkerberg listed rek. pris 129 against a current 105 with no discount
        styling at all. Treating it as an ordinarie would mark the product permanently
        on sale and fill Erbjudanden with deals that are not deals."""
        page = _page(
            price=_price(105.0, [_comparison("recommended", 129.0)], useDiscountStyling=False)
        )
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("105")
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.raw_response["ordinarie_type"] is None

    def test_package_content_sum_is_not_a_campaign(self) -> None:
        """ "Utan paketpris" is what a gift set's parts cost bought separately — a
        different basket, not this product's former price."""
        page = _page(
            price=_price(75.0, [_comparison("packageContentSum", 98.0)], useDiscountStyling=False)
        )
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("75")
        assert result.offer_price_sek is None

    def test_no_comparison_prices_means_no_offer(self) -> None:
        result = LykoExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.price_sek == Decimal("33")
        assert result.offer_price_sek is None
        assert result.offer_type is None

    def test_the_lowest_qualifying_ordinarie_wins(self) -> None:
        """A smaller claimed saving is the safer error: the ranking runs on what you pay
        either way, so an inflated ordinarie only makes a deal look better than it is."""
        page = _page(
            price=_price(
                50.0,
                [_comparison("ordinary", 120.0), _comparison("withoutCampaign", 90.0)],
            )
        )
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("90")

    def test_an_ordinarie_at_or_below_the_current_price_is_refused(self) -> None:
        """An offer is what you PAY, lower than price_sek by definition (v0.32.1). A
        comparison price that does not clear the current price is not a campaign."""
        page = _page(price=_price(58.0, [_comparison("ordinary", 58.0)]))
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("58")
        assert result.offer_price_sek is None

    def test_lyko_never_prints_a_jamforpris(self) -> None:
        """unitPrice was null on every product observed and no "Jämförpris" text exists
        on the site — the computed kr/unit is the only comparison figure."""
        result = LykoExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.store_unit_price_sek is None

    def test_a_missing_or_zero_price_returns_none(self) -> None:
        assert LykoExtractor().extract_from_html(_html(_page(price={})), URL) is None
        assert LykoExtractor().extract_from_html(_html(_page(price=_price(0.0))), URL) is None
        assert LykoExtractor().extract_from_html(_html(_page(price="33 kr")), URL) is None


class TestPackageEvidence:
    def test_size_and_unit_become_package_evidence(self) -> None:
        result = LykoExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.package_amount == Decimal("500")
        assert result.package_unit == "ml"
        assert result.pack_size is None

    def test_an_item_count_fills_pack_size_too(self) -> None:
        """ "10 st" is a count, the way quickadd reads the same text off a title."""
        page = _page(size=10.0, unit="st", sizeAndUnit="10 st")
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.package_amount == Decimal("10")
        assert result.package_unit == "st"
        assert result.pack_size == 10

    def test_a_gift_set_without_a_size_yields_no_evidence(self) -> None:
        """Live: "Hand Soap Bloomy Bliss 300 ml & Hand Soap Magnetic Mango 300 ml" carries
        size=None. Parsing the title would read 300 ml for 600 ml of product and halve
        its kr/liter — no evidence beats wrong evidence."""
        page = _page(size=None, unit=None, displayName="Hand Soap A 300 ml & Hand Soap B 300 ml")
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.package_amount is None
        assert result.package_unit is None
        assert result.pack_size is None

    def test_an_unknown_unit_is_ignored(self) -> None:
        page = _page(size=1.0, unit="pcs")
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.package_unit is None
        assert result.package_amount is None


class TestStockStatus:
    def test_schema_org_out_of_stock_is_respected(self) -> None:
        page = _page()
        page["linkingData"]["offers"]["availability"] = "https://schema.org/OutOfStock"
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.in_stock is False

    def test_all_schema_org_unbuyable_markers_are_respected(self) -> None:
        """The full JsonLdExtractor marker set, not just OutOfStock: schema.org spells
        "not buyable" as SoldOut and Discontinued too, and this tier outranks JSON-LD,
        so a miss here means the stricter judgement never runs."""
        for marker in ("https://schema.org/SoldOut", "http://schema.org/Discontinued"):
            page = _page()
            page["linkingData"]["offers"]["availability"] = marker
            result = LykoExtractor().extract_from_html(_html(page), URL)

            assert result is not None
            assert result.in_stock is False, marker

    def test_physical_store_availability_never_decides(self) -> None:
        """variantStoreAvailabilityStatus says "outOfStockInAllStores" on products that
        are in stock and on sale online — it describes shops we never buy from."""
        result = LykoExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.in_stock is True

    def test_interaction_type_answers_when_there_is_no_linking_data(self) -> None:
        buyable = _page(linkingData=None)
        sold_out = _page(linkingData=None, onlineStatus={"interactionType": "soldOut"})

        assert LykoExtractor().extract_from_html(_html(buyable), URL).in_stock is True
        assert LykoExtractor().extract_from_html(_html(sold_out), URL).in_stock is False

    def test_an_unknown_interaction_type_reads_as_in_stock(self) -> None:
        """A deny list with an optimistic default: a new Lyko enum value must not report
        the whole assortment as sold out."""
        page = _page(linkingData=None, onlineStatus={"interactionType": "buyWithSubscription"})

        assert LykoExtractor().extract_from_html(_html(page), URL).in_stock is True


class TestIdentity:
    def test_the_same_product_answers_on_the_category_path(self) -> None:
        """Verified live: fetching the category path returns 200 with canonicalUrl pointing
        at the brand path. Comparing whole paths would refuse a page that is fine — only
        the last segment is the product's identity."""
        category_url = (
            "https://lyko.com/sv/hudvard/kropp/handvard--fotvard/handtval/"
            "palmolive-hand-wash-refill-milk---honey-500-ml"
        )
        result = LykoExtractor().extract_from_html(_html(_page()), category_url)

        assert result is not None
        assert result.price_sek == Decimal("33")

    def test_a_query_string_or_trailing_slash_does_not_break_the_match(self) -> None:
        assert LykoExtractor().extract_from_html(_html(_page()), URL + "?variant=1") is not None
        assert LykoExtractor().extract_from_html(_html(_page()), URL + "/") is not None

    def test_a_percent_escaped_slug_matches(self) -> None:
        page = _page(canonicalUrl="/sv/bjork/björk-hand--body-wash-400-ml", url=None)
        escaped = "https://lyko.com/sv/bjork/bj%C3%B6rk-hand--body-wash-400-ml"

        assert LykoExtractor().extract_from_html(_html(page), escaped) is not None

    def test_a_sibling_variant_is_refused(self) -> None:
        """Size variants have their own slug AND their own price. If Lyko served the
        300 ml page while the link asks for 50 ml, recording it would attach another
        package's price to this link."""
        page = _page(
            variants=[
                {"code": "2244-183-0050", "url": "/sv/n.c.p.-olfactives/ncp-hand-cream-50ml"},
                {"code": "2244-183-0300", "url": "/sv/n.c.p.-olfactives/ncp-hand-wash-300ml"},
            ]
        )
        wanted = "https://lyko.com/sv/n.c.p.-olfactives/ncp-hand-cream-50ml"

        assert LykoExtractor().extract_from_html(_html(page), wanted) is None

    def test_a_foreign_slug_is_refused(self) -> None:
        other = "https://lyko.com/sv/palmolive/some-entirely-different-product"

        assert LykoExtractor().extract_from_html(_html(_page()), other) is None

    def test_the_url_field_answers_when_canonical_url_is_absent(self) -> None:
        page = _page(canonicalUrl=None)

        assert LykoExtractor().extract_from_html(_html(page), URL) is not None

    def test_a_url_without_a_path_is_refused(self) -> None:
        assert LykoExtractor().extract_from_html(_html(_page()), "https://lyko.com") is None


class TestStateParsing:
    def test_a_page_without_current_page_state_returns_none(self) -> None:
        html = "<html><body><script>window.APP_SHELL_DATA = {};</script></body></html>"

        assert LykoExtractor().extract_from_html(html, URL) is None

    def test_malformed_state_json_returns_none(self) -> None:
        html = "<html><body><script>window.CURRENT_PAGE = {not: valid, json};</script></body>"

        assert LykoExtractor().extract_from_html(html, URL) is None

    def test_an_unbalanced_state_object_returns_none(self) -> None:
        html = '<html><body><script>window.CURRENT_PAGE = {"canonicalUrl": "/x"</script></body>'

        assert LykoExtractor().extract_from_html(html, URL) is None

    def test_string_fields_containing_braces_do_not_break_the_scan(self) -> None:
        """A product description carrying { } or an escaped quote must not truncate the
        object — Lyko ships raw HTML descriptions in the state."""
        page = _page(
            text={"html": ['<p>Doft av honung {"inte" json} och \\"citrus\\"</p>']},
        )
        result = LykoExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("33")


class TestQuickAddMetadata:
    def test_identity_and_package_for_quick_add(self) -> None:
        """entryName is the abstract good — brand and size already removed — which is
        exactly the shape the product name field wants."""
        meta = LykoExtractor().extract_metadata_from_html(_html(_page()), URL)

        assert meta is not None
        assert meta.name == "Hand Wash Refill Milk & Honey"
        assert meta.brand == "Palmolive"
        assert meta.package_amount == Decimal("500")
        assert meta.package_unit == "ml"
        assert meta.in_stock is True
        assert meta.source == "lyko_page"
        # Lyko's taxonomy is a beauty tree, not this app's grocery sections.
        assert meta.category is None

    def test_metadata_shows_the_price_you_pay_today(self) -> None:
        page = _page(price=_price(58.0, [_comparison("ordinary", 290.0)]))
        meta = LykoExtractor().extract_metadata_from_html(_html(page), URL)

        assert meta is not None
        assert meta.price_sek == Decimal("58")

    def test_display_name_answers_when_entry_name_is_missing(self) -> None:
        meta = LykoExtractor().extract_metadata_from_html(_html(_page(entryName="")), URL)

        assert meta is not None
        assert meta.name == "Palmolive Hand Wash Refill Milk & Honey 500 ml"

    def test_metadata_without_a_name_returns_none(self) -> None:
        page = _page(entryName="", displayName="")

        assert LykoExtractor().extract_metadata_from_html(_html(page), URL) is None

    def test_metadata_for_a_foreign_slug_returns_none(self) -> None:
        other = "https://lyko.com/sv/palmolive/some-entirely-different-product"

        assert LykoExtractor().extract_metadata_from_html(_html(_page()), other) is None
