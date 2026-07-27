"""Tests for ClasOhlsonExtractor — the BEM price-markup store-HTML tier.

Fixtures mirror the live shapes verified 2026-07-27: Braun MultiQuick 44-6051 (rea —
JSON-LD price 499, markup ordinarie 999,00), JBL Tune 775NC (NBSP thousands separator,
"1 290,00") and Yes maskindiskmedel Pr446361000 (non-sale, jämförpris "(1,91/st)" that
the JSON-LD lacks). The extractor composes the shared JsonLdExtractor and only adds
what the markup knows; on any doubt it returns None and the ladder falls through to
plain JSON-LD.
"""

from decimal import Decimal

from domain.extractors.clasohlson import ClasOhlsonExtractor

URL = "https://www.clasohlson.com/se/Braun-MultiQuick-5-stavmixer/p/44-6051"
YES_URL = "https://www.clasohlson.com/se/Yes-Platinum-maskindiskmedel,-94-pack/p/Pr446361000"


def _jsonld(price: str, name: str = "Braun MultiQuick 5", availability: str = "InStock") -> str:
    return (
        '<script type="application/ld+json">{"@context": "https://schema.org",'
        f'"@type": "Product", "sku": "446051000", "name": "{name}",'
        '"brand": {"@type": "Brand", "name": "BRAUN"},'
        '"offers": {"@type": "Offer",'
        f'"availability": "https://schema.org/{availability}",'
        f'"price": "{price}", "priceCurrency": "SEK"}}}}</script>'
    )


def _price_section(sku: str, inner: str) -> str:
    return (
        '<div class="product__price-section"><div class="product__price clearfix">'
        f'<div class="product__normal-price {sku}">{inner}'
        '<span class="product__vat"> (inkl. moms) </span></div></div>'
    )


def _sale_markup(sku: str = "446051000", old: str = "999,00", new: str = "499,00") -> str:
    return _price_section(
        sku,
        f'<span class="product__old-price">{old}</span> '
        f'<span class="product__discount-price">{new} </span> ',
    )


def _normal_markup(sku: str, value: str, comparison: str | None = None) -> str:
    comp = f'<span class="product__comparison-price">({comparison})</span>' if comparison else ""
    return _price_section(sku, f'<span class="product__price-value">{value} {comp}</span> ')


class TestSale:
    def test_a_rea_records_ordinarie_as_price_and_current_as_offer(self) -> None:
        """The Braun shape: JSON-LD says only 499, the markup knows ordinarie 999."""
        html = _jsonld("499") + _sale_markup()
        result = ClasOhlsonExtractor().extract_from_html(html, URL, "Braun MultiQuick 5")

        assert result is not None
        assert result.price_sek == Decimal("999")
        assert result.offer_price_sek == Decimal("499")
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 500 kr"
        assert result.confidence == ClasOhlsonExtractor.CONFIDENCE
        assert result.raw_response["source"] == "clasohlson_page"

    def test_nbsp_thousands_separator_reads_as_one_number(self) -> None:
        """The JBL shape: "1&nbsp;290,00" must be 1290, not 1."""
        html = _jsonld("690") + _sale_markup(old="1&nbsp;290,00", new="690,00")
        result = ClasOhlsonExtractor().extract_from_html(html, URL)

        assert result is not None
        assert result.price_sek == Decimal("1290")
        assert result.offer_price_sek == Decimal("690")

    def test_availability_still_comes_from_the_jsonld(self) -> None:
        """Composition, not duplication: stock stays the JSON-LD extractor's answer."""
        html = _jsonld("499", availability="OutOfStock") + _sale_markup()
        result = ClasOhlsonExtractor().extract_from_html(html, URL)

        assert result is not None
        assert result.in_stock is False


class TestComparisonPrice:
    def test_the_printed_jamforpris_fills_store_unit_price(self) -> None:
        """The Yes shape: "(1,91/st)" exists only in the markup — JSON-LD has none."""
        html = _jsonld("179.9", name="Yes maskindiskmedel") + _normal_markup(
            "446361000", "179,90", "1,91/st"
        )
        result = ClasOhlsonExtractor().extract_from_html(html, YES_URL)

        assert result is not None
        assert result.store_unit_price_sek == Decimal("1.91")
        assert result.price_sek == Decimal("179.9")
        assert result.offer_price_sek is None  # no rea invented

    def test_markup_with_nothing_beyond_jsonld_defers_to_that_tier(self) -> None:
        """No old price, no jämförpris → None, so plain JSON-LD answers (identical
        price, higher standing confidence for the structured node)."""
        html = _jsonld("179.9") + _normal_markup("446051000", "179,90")
        assert ClasOhlsonExtractor().extract_from_html(html, URL) is None


class TestSelfChecks:
    """A wrong 0.97-confidence price is worse than falling through."""

    def test_markup_price_disagreeing_with_jsonld_is_refused(self) -> None:
        """The anchored block belongs to some other product, or the shape changed."""
        html = _jsonld("499") + _sale_markup(new="479,00")
        assert ClasOhlsonExtractor().extract_from_html(html, URL) is None

    def test_a_foreign_sku_block_is_not_anchored(self) -> None:
        """Carousel price markup carries other SKUs — the URL's digits must match."""
        html = _jsonld("499") + _sale_markup(sku="999999000")
        assert ClasOhlsonExtractor().extract_from_html(html, URL) is None

    def test_no_jsonld_to_compose_with_returns_none(self) -> None:
        assert ClasOhlsonExtractor().extract_from_html(_sale_markup(), URL) is None

    def test_a_url_without_a_product_code_returns_none(self) -> None:
        html = _jsonld("499") + _sale_markup()
        assert (
            ClasOhlsonExtractor().extract_from_html(html, "https://www.clasohlson.com/se/") is None
        )

    def test_metadata_defers_to_jsonld(self) -> None:
        """Clas Ohlson's JSON-LD names carry the package text — quick-add needs nothing
        from the markup."""
        html = _jsonld("499") + _sale_markup()
        assert ClasOhlsonExtractor().extract_metadata_from_html(html, URL) is None
