"""Tests for RustaExtractor — the window.CURRENT_PAGE page-state tier.

The fixtures mirror the live shapes verified 2026-07-27: Hängstol Sorrento (rea —
current 1899.00 / original 2999.00, which the JSON-LD hides), Deo roll-on Nivea
(comparisonPrice 498.0 "/liter", subTitle carrying the package text) and Matlåda
(three color variations sharing one page). The extractor's job is to keep a Rusta
rea honest under the ORDINARIE + OFFER campaign model — the JSON-LD-only path would
record the campaign price as price_sek, the exact inversion v0.25.2/v0.25.3 fixed
in the other store families.
"""

import json
from decimal import Decimal

from domain.extractors.rusta import RustaExtractor

URL = "https://www.rusta.com/sv-se/tradgard-och-utemobler/hangstol-sorrento-605011720101"
CODE = "605011720101"

# A decoy that precedes CURRENT_PAGE in every live page: the app shell carries no product
# data, and an extractor that grabs "the first window.* JSON" would read it by mistake.
_APP_SHELL = 'window.APP_SHELL_DATA = {"siteSettings": {"culture": "sv-se"}, "cart": {}};'


def _price(
    current: float,
    original: float | None = None,
    comparison: float = 0.0,
    comparison_unit: str | None = None,
) -> dict:
    """A price object in the live CURRENT_PAGE shape."""
    original = original if original is not None else current
    return {
        "current": {"inclVat": current, "exclVat": round(current * 0.8, 2), "vatPercent": 25.00},
        "original": {
            "inclVat": original,
            "exclVat": round(original * 0.8, 2),
            "vatPercent": 25.00,
        },
        "previous": None,
        "comparisonPrice": comparison,
        "comparisonUnit": comparison_unit,
        "multiPrice": None,
        "memberCurrent": None,
        "memberOriginal": None,
        "hideOriginalPrice": False,
        "boxPrice": 0.0,
        "originalBoxPrice": 0.0,
    }


def _page(**overrides) -> dict:
    page = {
        "canonicalUrl": URL,
        "displayName": "Hängstol Sorrento",
        "subTitle": "105x190 cm Mörkgrå Konstrotting",
        "brandName": "Rusta",
        "variationCode": CODE,
        "code": CODE,
        "price": _price(1899.0, 2999.0),
        "stock": "high",
        "isSale": True,
        "discontinued": False,
        "buyableOnline": True,
        "variations": [],
    }
    page.update(overrides)
    return page


def _html(page: dict) -> str:
    return (
        "<html><head></head><body><div>sida</div>"
        f"<script> window.CURRENT_VERSION = '14.0.71'; {_APP_SHELL} "
        f"window.CURRENT_PAGE = {json.dumps(page, ensure_ascii=False)};</script>"
        "</body></html>"
    )


class TestPriceExtraction:
    def test_a_rea_records_ordinarie_as_price_and_current_as_offer(self) -> None:
        """The reason this extractor exists: JSON-LD says only 1899, CURRENT_PAGE knows
        the ordinarie is 2999 — price_sek must be the ordinarie, kampanj the offer."""
        result = RustaExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.price_sek == Decimal("2999")
        assert result.offer_price_sek == Decimal("1899")
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 1100 kr"
        assert result.confidence == RustaExtractor.CONFIDENCE
        assert result.raw_response["source"] == "rusta_page"

    def test_no_sale_when_original_equals_current(self) -> None:
        """Outside a campaign Rusta sets original == current — no invented offer."""
        page = _page(price=_price(24.9), isSale=False)
        result = RustaExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("24.9")
        assert result.offer_price_sek is None
        assert result.offer_type is None

    def test_comparison_price_is_the_printed_jamforpris(self) -> None:
        """comparisonPrice 498.0 renders as "Jämförpris 498 kr /liter" on the page —
        the PRINTED value the store_unit_price_sek field is defined to carry."""
        page = _page(price=_price(24.9, comparison=498.0, comparison_unit="/liter"))
        result = RustaExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.store_unit_price_sek == Decimal("498")

    def test_zero_comparison_price_means_none_printed(self) -> None:
        result = RustaExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.store_unit_price_sek is None

    def test_stock_none_is_out_of_stock_and_high_is_in(self) -> None:
        extractor = RustaExtractor()

        out = extractor.extract_from_html(_html(_page(stock="none")), URL)
        assert out is not None and out.in_stock is False

        in_stock = extractor.extract_from_html(_html(_page(stock="high")), URL)
        assert in_stock is not None and in_stock.in_stock is True

    def test_package_evidence_comes_from_the_subtitle(self) -> None:
        """The deo shape: subTitle "50 ml Invisible Black & White" is the only place the
        package text exists (the JSON-LD name is bare)."""
        page = _page(subTitle="50 ml Invisible Black & White")
        result = RustaExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.package_amount == Decimal("50")
        assert result.package_unit == "ml"

    def test_a_furniture_subtitle_yields_no_package_evidence(self) -> None:
        """ "105x190 cm Mörkgrå Konstrotting" has no parseable package unit — all-None
        beats wrong evidence flagged against the operator's typed intent."""
        result = RustaExtractor().extract_from_html(_html(_page()), URL)

        assert result is not None
        assert result.package_amount is None
        assert result.package_unit is None
        assert result.pack_size is None


class TestNodeIdentity:
    def test_a_foreign_code_is_refused(self) -> None:
        """The state describing some OTHER product must not be recorded with 0.98
        confidence — the URL's article code is the identity check."""
        page = _page(code="999999999999", variationCode="999999999999")
        assert RustaExtractor().extract_from_html(_html(page), URL) is None

    def test_a_variation_match_uses_the_variations_price(self) -> None:
        """Color siblings share one page: the URL's code can sit in variations[] with its
        own price and stock rather than at the root."""
        variation_url = "https://www.rusta.com/sv-se/forvaring/matlada-1-l-3-pack-gron-803513630103"
        page = _page(
            code="803513630101",
            variationCode="803513630101",
            subTitle="1 l 3-pack",
            variations=[
                {"code": "803513630103", "price": _price(59.9), "stock": "none"},
            ],
        )
        result = RustaExtractor().extract_from_html(_html(page), variation_url)

        assert result is not None
        assert result.price_sek == Decimal("59.9")
        assert result.in_stock is False

    def test_a_url_without_an_article_code_is_refused(self) -> None:
        assert (
            RustaExtractor().extract_from_html(_html(_page()), "https://www.rusta.com/sv-se/")
            is None
        )


class TestDegradation:
    """Every parse failure returns None so the ladder falls through to JSON-LD — right
    price outside campaigns — rather than failing the check."""

    def test_a_page_without_current_page_state_returns_none(self) -> None:
        html = f"<html><body><script>{_APP_SHELL}</script></body></html>"
        assert RustaExtractor().extract_from_html(html, URL) is None

    def test_malformed_state_json_returns_none(self) -> None:
        html = "<script>window.CURRENT_PAGE = {broken json};</script>"
        assert RustaExtractor().extract_from_html(html, URL) is None

    def test_a_missing_price_object_returns_none(self) -> None:
        assert RustaExtractor().extract_from_html(_html(_page(price=None)), URL) is None

    def test_string_fields_containing_braces_do_not_break_the_scan(self) -> None:
        """The balanced-brace reader must be string-aware: descriptions can carry '}'."""
        page = _page(displayName='Hängstol "Sorrento" }{')
        result = RustaExtractor().extract_from_html(_html(page), URL)

        assert result is not None
        assert result.price_sek == Decimal("2999")


class TestMetadata:
    def test_identity_and_package_for_quick_add(self) -> None:
        """The deo shape end-to-end: name, brand, current price for display, and the
        package prefilled from subTitle — none of which the bare JSON-LD provides."""
        page = _page(
            displayName="Deo roll-on Nivea",
            subTitle="50 ml Invisible Black & White",
            brandName="Nivea",
            price=_price(24.9, comparison=498.0, comparison_unit="/liter"),
            isSale=False,
        )
        meta = RustaExtractor().extract_metadata_from_html(_html(page), URL)

        assert meta is not None
        assert meta.name == "Deo roll-on Nivea"
        assert meta.brand == "Nivea"
        assert meta.price_sek == Decimal("24.9")
        assert meta.package_amount == Decimal("50")
        assert meta.package_unit == "ml"
        assert meta.source == "rusta_page"
        assert meta.in_stock is True

    def test_metadata_shows_the_price_you_pay_today(self) -> None:
        """Preview display only — the rea price is what the user sees on the page."""
        meta = RustaExtractor().extract_metadata_from_html(_html(_page()), URL)

        assert meta is not None
        assert meta.price_sek == Decimal("1899")

    def test_metadata_without_a_name_returns_none(self) -> None:
        meta = RustaExtractor().extract_metadata_from_html(_html(_page(displayName="")), URL)
        assert meta is None
