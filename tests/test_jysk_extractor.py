"""Tests for JyskExtractor — the ProductGroup resolver + price-markup store-HTML tier.

Fixtures mirror the live shapes verified 2026-08-16, including the React ``<!-- -->``
separators that sit between a label and its value:

- Badlakan NORA 100x150 sand (article 2342607) — a 24-variant ``ProductGroup`` whose
  JSON-LD says 299 while the headline prints the flerköp "2 för 369:-" and the piece
  price moves to ``singlepieceprice``.
- Badrumsmatta SANDHEM 50x80 (article 2522951) — a single-variant ``Product`` at a rea:
  JSON-LD publishes 75, and only the markup knows "Ordinarie pris: 149:- /st.".
- Bokhylla MOSBJERG — the plain shape, no campaign at all.

The generic JsonLdExtractor answers None on the first and 75-as-ordinarie on the second;
both are why this tier exists.
"""

from decimal import Decimal

import pytest

from domain.extractors.jsonld import JsonLdExtractor
from domain.extractors.jysk import JyskExtractor

NORA_URL = "https://jysk.se/badrum/handdukar/badlakan-nora-100x150-sand?article=2342607"
SANDHEM_URL = "https://jysk.se/badrum/badrumsmattor/badrumsmatta-sandhem-50x80-mork-sand"


def _group(
    *,
    article: str = "2342607",
    price: str = "299",
    name: str = "Badlakan NORA 100x150 sand",
) -> str:
    """A ProductGroup whose FIRST variant is the one the URL names, siblings named bare."""
    return (
        '<script type="application/ld+json">{"@context": "https://schema.org/",'
        '"@type": "ProductGroup", "name": "NORA Handdukar",'
        '"brand": {"@type": "Brand", "name": "KRONBORG"},'
        '"hasVariant": ['
        f'{{"@type": "Product", "name": "{name}", "sku": "{article}",'
        f'"offers": {{"@type": "Offer", "priceCurrency": "SEK", "price": "{price}"}},'
        '"size": "100x150"},'
        '{"@type": "Product", "name": "NORA", "sku": "2132701",'
        '"offers": {"@type": "Offer", "priceCurrency": "SEK", "price": "79.95"},'
        '"size": "50x70"}]}</script>'
    )


def _product(*, article: str = "2522951", price: str = "75") -> str:
    return (
        '<script type="application/ld+json">{"@context": "https://schema.org/",'
        '"@type": "Product", "name": "Badrumsmatta SANDHEM 50x80 mörk sand",'
        f'"sku": "{article}", "brand": {{"@type": "Brand", "name": "JYSK"}},'
        f'"offers": {{"@type": "Offer", "priceCurrency": "SEK", "price": "{price}"}}}}</script>'
    )


def _block(inner: str) -> str:
    """The PDP price container, closed by the <hr/> that follows it on the live page."""
    return f'<div class="jysk-ui-shim"><div class="pdp-product-price">{inner}</div></div><hr/>'


def _plain(value: str = "1299:-") -> str:
    return _block(
        '<div class="product-pdp-price-wrapper"><div class="product-price-wrapper d-block">'
        '<div class="product-price text-bold" aria-description="Pris">'
        f'<span class="product-price-value">{value}</span>'
        '<span class="unit product-price-unit product-price-unit-current">/st.</span>'
        "</div></div></div>"
    )


def _multi_buy(label: str = "2 för 369:-", piece: str = "299:- /st.") -> str:
    return _block(
        '<div class="product-pdp-price-wrapper"><div class="product-price-wrapper d-block">'
        '<div class="product-price text-bold" aria-description="Pris">'
        f'<span class="product-price-value">{label}</span> </div>'
        '<span class="ssr-product-price-support d-block price-piece text-regular '
        f'singlepieceprice">{piece}</span>'
        "</div></div>"
    )


def _rea(
    current: str = "75:-",
    ordinarie: str = "149:- /st.",
    lowest: str = "149:- /st. (-50%)",
) -> str:
    return _block(
        '<div class="d-flex pdp-sticker-container"><div class="discount-label">'
        '<div class="sticker sticker-discount">'
        '<span class="sticker-text">-50%</span></div></div></div>'
        '<div class="product-pdp-price-wrapper"><div class="product-price-wrapper d-block">'
        '<div class="product-price discountprice text-bold" aria-description="Pris">'
        f'<span class="product-price-value">{current}</span>'
        '<span class="unit product-price-unit product-price-unit-current">/st.</span></div>'
        '<div class="product-price-cheapest-price-notice">'
        f"<div>Lägsta pris 30 dagar:<!-- --> <!-- -->{lowest}</div>"
        f"<div>Ordinarie pris:<!-- --> <!-- -->{ordinarie} </div></div>"
        "</div></div>"
    )


class TestWhyThisTierExists:
    def test_generic_jsonld_cannot_see_a_productgroup(self) -> None:
        """The 24-variant page returns None from the generic tier — it would hit the LLM."""
        assert JsonLdExtractor().extract_from_html(_group() + _multi_buy(), "Badlakan NORA") is None

    def test_generic_jsonld_records_a_rea_as_the_ordinarie(self) -> None:
        """JYSK publishes the CAMPAIGN price, so the generic tier inverts ordinarie/offer."""
        base = JsonLdExtractor().extract_from_html(_product() + _rea(), "Badrumsmatta SANDHEM")

        assert base is not None
        assert base.price_sek == Decimal("75")  # the sale price, recorded as if it were normal
        assert base.offer_price_sek is None


class TestVariantResolution:
    def test_the_url_article_picks_its_variant_out_of_the_group(self) -> None:
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), NORA_URL)

        assert result is not None
        assert result.raw_response["article"] == "2342607"
        assert result.raw_response["name"] == "Badlakan NORA 100x150 sand"
        assert result.raw_response["source"] == "jysk_page"

    def test_an_article_absent_from_the_group_is_refused(self) -> None:
        """A stale or moved URL must not silently record a sibling variant's price."""
        url = "https://jysk.se/badrum/handdukar/badlakan-nora-100x150-sand?article=9999999"

        assert JyskExtractor().extract_from_html(_group() + _multi_buy(), url) is None

    def test_a_bare_path_takes_the_first_variant_when_the_markup_agrees(self) -> None:
        """No ?article=: the path selected the variant and JYSK publishes it first — the
        printed piece price is what proves the pick."""
        url = "https://jysk.se/badrum/handdukar/badlakan-nora-100x150-sand"
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), url)

        assert result is not None
        assert result.price_sek == Decimal("299")

    def test_a_bare_path_with_no_readable_price_block_is_refused(self) -> None:
        """Neither identity signal available — a wrong variant would ride at 0.97."""
        url = "https://jysk.se/badrum/handdukar/badlakan-nora-100x150-sand"

        assert JyskExtractor().extract_from_html(_group(), url) is None

    def test_a_single_variant_product_node_is_read_directly(self) -> None:
        result = JyskExtractor().extract_from_html(_product() + _rea(), SANDHEM_URL)

        assert result is not None
        assert result.raw_response["article"] == "2522951"

    def test_a_page_publishing_a_different_article_is_refused(self) -> None:
        url = "https://jysk.se/badrum/badrumsmattor/badrumsmatta-sandhem-50x80-mork-sand?article=1111111"

        assert JyskExtractor().extract_from_html(_product() + _rea(), url) is None

    def test_a_disagreeing_printed_price_falls_through(self) -> None:
        """The block belongs to something else, or the markup changed shape."""
        html = _group(price="299") + _multi_buy(piece="499:- /st.")

        assert JyskExtractor().extract_from_html(html, NORA_URL) is None


class TestRea:
    def test_ordinarie_becomes_the_price_and_the_current_becomes_the_offer(self) -> None:
        result = JyskExtractor().extract_from_html(_product() + _rea(), SANDHEM_URL)

        assert result is not None
        assert result.price_sek == Decimal("149")
        assert result.offer_price_sek == Decimal("75")
        assert result.offer_type == "kampanj"
        assert result.offer_details == "Spara 74 kr"

    def test_the_thirty_day_low_alone_never_makes_a_campaign(self) -> None:
        """ "Lägsta pris 30 dagar" is a legal disclosure about a WINDOW, not an ordinarie:
        reading it as one would invent a campaign every time the two lines differ."""
        html = _product(price="75") + _block(
            '<div class="product-price discountprice text-bold">'
            '<span class="product-price-value">75:-</span></div>'
            '<div class="product-price-cheapest-price-notice">'
            "<div>Lägsta pris 30 dagar:<!-- --> <!-- -->200:- /st.</div></div>"
        )
        result = JyskExtractor().extract_from_html(html, SANDHEM_URL)

        assert result is not None
        assert result.price_sek == Decimal("75")
        assert result.offer_price_sek is None

    def test_an_ordinarie_at_or_below_the_current_price_is_refused(self) -> None:
        """v0.32.1: an offer is what you PAY, below the ordinarie by definition."""
        result = JyskExtractor().extract_from_html(
            _product(price="149") + _rea(current="149:-", ordinarie="149:- /st."), SANDHEM_URL
        )

        assert result is not None
        assert result.price_sek == Decimal("149")
        assert result.offer_price_sek is None
        assert result.offer_type is None


class TestFlerkop:
    def test_a_multi_buy_label_becomes_a_per_unit_offer(self) -> None:
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), NORA_URL)

        assert result is not None
        assert result.price_sek == Decimal("299")
        assert result.offer_price_sek == Decimal("184.50")
        assert result.offer_type == "kampanj"

    def test_the_label_rides_verbatim_as_the_villkor(self) -> None:
        """The price only exists on condition that you buy N (v0.41.2)."""
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), NORA_URL)

        assert result is not None
        assert result.offer_details == "2 för 369:-"

    def test_an_uneven_split_quantizes_to_ore(self) -> None:
        """ "3 för 95" is 31.666… — half-up at the boundary, like everywhere else."""
        html = _group(price="40") + _multi_buy(label="3 för 95:-", piece="40:- /st.")
        result = JyskExtractor().extract_from_html(html, NORA_URL)

        assert result is not None
        assert result.offer_price_sek == Decimal("31.67")

    def test_a_count_without_a_currency_marker_is_not_a_price(self) -> None:
        """ "3 för 2" states no total: parsed as a price it would record 0.67 and become
        the product's floor for 84 days — the v0.49.1 bug, in JYSK's wording."""
        html = _group(price="299") + _multi_buy(label="3 för 2", piece="299:- /st.")
        result = JyskExtractor().extract_from_html(html, NORA_URL)

        assert result is not None
        assert result.price_sek == Decimal("299")
        assert result.offer_price_sek is None

    def test_a_multi_buy_that_is_not_cheaper_per_unit_is_refused(self) -> None:
        html = _group(price="150") + _multi_buy(label="2 för 400:-", piece="150:- /st.")
        result = JyskExtractor().extract_from_html(html, NORA_URL)

        assert result is not None
        assert result.offer_price_sek is None


class TestPlainAndInvariants:
    def test_a_product_with_no_campaign_reports_the_plain_price(self) -> None:
        html = _product(article="2522951", price="1299") + _plain()
        url = "https://jysk.se/forvaring/hyllor/bokhylla-mosbjerg?article=2522951"
        result = JyskExtractor().extract_from_html(html, url)

        assert result is not None
        assert result.price_sek == Decimal("1299")
        assert result.offer_price_sek is None
        assert result.raw_response["source"] == "jysk_page"

    def test_a_thousands_separated_price_parses_as_one_number(self) -> None:
        html = _product(article="2522951", price="1299") + _plain(value="1 299:-")
        url = "https://jysk.se/forvaring/hyllor/bokhylla-mosbjerg?article=2522951"
        result = JyskExtractor().extract_from_html(html, url)

        assert result is not None
        assert result.price_sek == Decimal("1299")

    def test_no_jamforpris_is_ever_reported(self) -> None:
        """JYSK prints only "/st." — the computed kr/enhet is the sole comparison figure."""
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), NORA_URL)

        assert result is not None
        assert result.store_unit_price_sek is None

    def test_dimensions_are_never_read_as_a_package_amount(self) -> None:
        """ "100x150" is a towel's size in cm, not an amount of product."""
        result = JyskExtractor().extract_from_html(_group() + _multi_buy(), NORA_URL)

        assert result is not None
        assert result.package_amount is None
        assert result.package_unit is None
        assert result.pack_size is None

    def test_a_page_without_product_jsonld_falls_through(self) -> None:
        assert JyskExtractor().extract_from_html("<html><body>nope</body></html>", NORA_URL) is None

    @pytest.mark.parametrize("currency", ["EUR", "DKK"])
    def test_a_foreign_currency_is_refused(self, currency: str) -> None:
        html = (
            '<script type="application/ld+json">{"@type": "Product", "name": "X",'
            '"sku": "2522951", "offers": {"@type": "Offer", "price": "75",'
            f'"priceCurrency": "{currency}"}}}}</script>'
        ) + _plain(value="75:-")

        assert JyskExtractor().extract_from_html(html, SANDHEM_URL) is None


class TestMetadata:
    def test_quick_add_gets_the_variants_own_name_not_the_groups(self) -> None:
        """Siblings are named bare after the group ("NORA"); only the URL's variant
        carries a name that names a purchasable article."""
        meta = JyskExtractor().extract_metadata_from_html(_group() + _multi_buy(), NORA_URL)

        assert meta is not None
        assert meta.name == "Badlakan NORA 100x150 sand"
        assert meta.brand == "KRONBORG"
        assert meta.price_sek == Decimal("299")
        assert meta.source == "jysk_page"

    def test_stock_is_reported_as_unknown_not_invented(self) -> None:
        """JYSK publishes no server-side availability anywhere."""
        meta = JyskExtractor().extract_metadata_from_html(_group() + _multi_buy(), NORA_URL)

        assert meta is not None
        assert meta.in_stock is None

    def test_metadata_refuses_the_same_pages_the_price_tier_refuses(self) -> None:
        url = "https://jysk.se/badrum/handdukar/badlakan-nora-100x150-sand?article=9999999"

        assert JyskExtractor().extract_metadata_from_html(_group(), url) is None
