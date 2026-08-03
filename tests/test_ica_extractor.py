"""Tests for IcaExtractor — the window.__QUERY_INITIAL_STATE__ page-state tier.

The fixtures mirror the live shape verified 2026-08-03: JätteFranska Rostbröd at ICA
Maxi Sandviken — JSON-LD price 27.30 with no campaign in sight while the state carries
promotions ("2 för 45 kr", requiredProductQuantity 2) and the printed jämförpris
(24.82 kr/kg). ICA promotions carry NO price field, so the per-unit offer is parsed
from the store's own label under the v0.41.2 doctrine: agree with
requiredProductQuantity or refuse, never guess at a multi-buy whose label does not
parse, and refuse an offer at or above ordinarie wholesale (v0.32.1).
"""

import json
from decimal import Decimal

from domain.extractors.ica import IcaExtractor

URL = (
    "https://handlaprivatkund.ica.se/stores/1003396/products/"
    "j%C3%A4ttefranska-rostbr%C3%B6d-1-1kg-p%C3%A5gen/2010293"
)
PRODUCT_ID = "2010293"


def _promotion(description: str, required_quantity: int | None = None) -> dict:
    """A promotion in the live state shape — note: no price field anywhere."""
    promo = {
        "promoId": "234b1f78-9cbb-4199-b69c-ba8b261af9ea",
        "retailerPromotionId": "5004080311-1782816723",
        "description": description,
        "type": "OFFER",
        "presentationMode": "DEFAULT",
        "limitReached": False,
    }
    if required_quantity is not None:
        promo["requiredProductQuantity"] = required_quantity
    return promo


def _product(**overrides) -> dict:
    product = {
        "productId": "c127a67a-aa18-42f9-b586-08c8ba17f95e",
        "retailerProductId": PRODUCT_ID,
        "type": "REGULAR",
        "name": "JätteFranska Rostbröd 1,1kg Pågen",
        "brand": "Pågen",
        "packSizeDescription": "1.1kg",
        "price": {"amount": "27.30", "currency": "SEK"},
        "unitPrice": {
            "price": {"amount": "24.82", "currency": "SEK"},
            "unit": "fop.price.per.kg",
        },
        "available": True,
        "promotions": [_promotion("2 för 45 kr", required_quantity=2)],
        "categoryPath": ["Bröd & Kakor", "Matbröd", "Ljust bröd", "Rostbröd"],
    }
    product.update(overrides)
    return product


def _html(product: dict) -> str:
    """The react-query dehydrated nesting the live page serves the product under."""
    state = {
        "mutations": [],
        "queries": [{"dehydratedAt": 1785759280011, "state": {"data": {"product": product}}}],
    }
    return (
        '<html><head><script>window.__APP_CONFIG__={"locale": "sv-SE"};</script>'
        "</head><body><div>sida</div>"
        f"<script>window.__QUERY_INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}"
        "</script></body></html>"
    )


class TestCampaignExtraction:
    def test_multi_buy_promotion_becomes_per_unit_offer(self):
        result = IcaExtractor().extract_from_html(_html(_product()), URL)
        assert result is not None
        assert result.price_sek == Decimal("27.30")
        assert result.offer_price_sek == Decimal("22.50")
        assert result.offer_type == "kampanj"
        assert result.offer_details == "2 för 45 kr"
        assert result.raw_response["source"] == "ica_page"

    def test_uneven_total_quantizes_to_ore_half_up(self):
        product = _product(
            price={"amount": "39.90", "currency": "SEK"},
            promotions=[_promotion("3 för 95 kr", required_quantity=3)],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek == Decimal("31.67")

    def test_no_promotions_means_no_offer(self):
        product = _product(promotions=[])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.price_sek == Decimal("27.30")
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None

    def test_label_contradicting_required_quantity_is_refused(self):
        product = _product(promotions=[_promotion("2 för 45 kr", required_quantity=3)])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_multi_buy_quantity_with_unparseable_label_is_refused(self):
        # A multi-buy whose per-unit price is unknowable gets NO offer, never a guess.
        product = _product(promotions=[_promotion("Välj & blanda!", required_quantity=2)])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_saving_label_is_not_a_price(self):
        # "Spara 5 kr" states the discount, not what you pay — 5.00 as the offer would
        # be below ordinarie and pass the inversion guard, which is why it needs its own.
        product = _product(promotions=[_promotion("Spara 5 kr", required_quantity=1)])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_stammis_price_label(self):
        product = _product(promotions=[_promotion("Stammispris 20 kr", required_quantity=1)])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek == Decimal("20.00")
        assert result.offer_type == "stammispris"

    def test_offer_at_or_above_ordinarie_is_refused_wholesale(self):
        # "2 för 60" on a 27.30 product parses to 30.00 — the v0.32.1 inversion.
        product = _product(promotions=[_promotion("2 för 60 kr", required_quantity=2)])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None

    def test_per_measure_label_converts_through_the_jamforpris(self):
        # Live catchweight shape 2026-08-03: chicken ca 925 g, ordinarie 150.91,
        # jämförpris 163.15 kr/kg, campaign "109 kr/kg". The package offer is
        # 109 × 150.91 / 163.15 = 100.82 — the store's own arithmetic; recording the
        # bare 109.00 would understate nothing and pass the inversion guard while
        # being wrong by the package weight.
        product = _product(
            price={"amount": "150.91", "currency": "SEK"},
            unitPrice={
                "price": {"amount": "163.15", "currency": "SEK"},
                "unit": "fop.price.per.kg",
            },
            promotions=[_promotion("109 kr/kg")],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.price_sek == Decimal("150.91")
        assert result.offer_price_sek == Decimal("100.82")
        assert result.offer_details == "109 kr/kg"

    def test_per_litre_label_converts_including_deposit_suffix(self):
        product = _product(
            price={"amount": "137.45", "currency": "SEK"},
            unitPrice={
                "price": {"amount": "20.83", "currency": "SEK"},
                "unit": "fop.price.per.litre.without.deposit",
            },
            promotions=[_promotion("15 kr/l")],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek == Decimal("98.98")

    def test_per_measure_label_against_wrong_jamforpris_unit_is_refused(self):
        product = _product(
            unitPrice={
                "price": {"amount": "24.82", "currency": "SEK"},
                "unit": "fop.price.per.kg",
            },
            promotions=[_promotion("5 kr/l")],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_per_measure_label_without_jamforpris_is_refused(self):
        product = _product(unitPrice=None, promotions=[_promotion("10 kr/kg")])
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_per_measure_label_with_multi_buy_condition_is_refused(self):
        # "2 för 109 kr/kg" mixes bases, and a min-quantity condition on a per-measure
        # price cannot ride along visibly.
        product = _product(
            promotions=[
                _promotion("2 för 20 kr/kg"),
                _promotion("15 kr/kg", required_quantity=2),
            ]
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek is None

    def test_trailing_slash_with_no_measure_is_a_package_price(self):
        # Live 2026-08-03: a 20-pack cola with the truncated label "90 kr/" —
        # ordinarie 137.45, campaign 90 kr for the package.
        product = _product(
            price={"amount": "137.45", "currency": "SEK"},
            promotions=[_promotion("90 kr/")],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek == Decimal("90.00")

    def test_several_promotions_pick_the_highest_per_unit(self):
        # The smaller claimed saving is the safer error (Lyko's rule).
        product = _product(
            promotions=[
                _promotion("2 för 40 kr", required_quantity=2),
                _promotion("2 för 45 kr", required_quantity=2),
            ]
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.offer_price_sek == Decimal("22.50")
        assert result.offer_details == "2 för 45 kr"


class TestPageFacts:
    def test_printed_jamforpris_and_package_evidence(self):
        result = IcaExtractor().extract_from_html(_html(_product()), URL)
        assert result is not None
        assert result.store_unit_price_sek == Decimal("24.82")
        assert result.package_amount == Decimal("1.1")
        assert result.package_unit == "kg"

    def test_range_pack_size_yields_no_package_evidence(self):
        # Catchweight ranges name no single amount; the parser would read the lower
        # bound ("766.4g") as the package.
        product = _product(packSizeDescription="766.4g - 1000000g")
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.package_amount is None
        assert result.package_unit is None

    def test_unavailable_product_is_out_of_stock(self):
        product = _product(available=False)
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.in_stock is False

    def test_missing_available_defaults_to_in_stock(self):
        product = _product()
        del product["available"]
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.in_stock is True


class TestIdentityAndFallback:
    def test_url_id_mismatch_falls_back(self):
        # The state describes some OTHER product (alternatives, recommendations) —
        # never record a foreign price at 0.98 confidence.
        other_url = URL.replace(PRODUCT_ID, "9999999")
        assert IcaExtractor().extract_from_html(_html(_product()), other_url) is None

    def test_sibling_product_in_state_is_not_the_identity(self):
        product = _product(
            alternatives=[_product(retailerProductId="8888888")],
        )
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.price_sek == Decimal("27.30")

    def test_page_without_state_falls_back(self):
        html = "<html><body><p>Ingen hydration här</p></body></html>"
        assert IcaExtractor().extract_from_html(html, URL) is None

    def test_malformed_state_falls_back(self):
        html = '<script>window.__QUERY_INITIAL_STATE__={"queries": [</script>'
        assert IcaExtractor().extract_from_html(html, URL) is None

    def test_url_without_numeric_id_falls_back(self):
        url = "https://handlaprivatkund.ica.se/stores/1003396/products/nagot-utan-id"
        assert IcaExtractor().extract_from_html(_html(_product()), url) is None

    def test_description_with_braces_survives_the_scanner(self):
        product = _product(name='Bröd {"med": konstiga} tecken \\" i beskrivningen')
        result = IcaExtractor().extract_from_html(_html(product), URL)
        assert result is not None
        assert result.price_sek == Decimal("27.30")


class TestMetadata:
    def test_metadata_is_deliberately_none(self):
        # ICA's JSON-LD carries name, brand and size — the quick-add preview's JSON-LD
        # tier answers everything, so this tier adds nothing there.
        assert IcaExtractor().extract_metadata_from_html(_html(_product()), URL) is None
