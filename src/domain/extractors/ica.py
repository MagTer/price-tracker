"""ICA page-state price extractor.

ICA (handlaprivatkund.ica.se) server-renders a schema.org JSON-LD node AND a react-query
hydration state (``window.__QUERY_INITIAL_STATE__``). The JSON-LD carries only the
current per-unit price — a campaign lives ONLY in the state's ``promotions`` list
(verified live 2026-08-03, JätteFranska at ICA Maxi Sandviken: JSON-LD price 27.30 with
no offer in sight while the page shows "2 för 45 kr"), and the printed jämförpris
(``unitPrice`` — 24.82 kr/kg) is absent from the JSON-LD entirely. So the JSON-LD tier
records the right ordinarie but is structurally blind to every ICA deal — the same
blind spot Rusta and Lyko had, solved the same way.

Unlike Willys' API, an ICA promotion carries NO price field — only the label text
("2 för 45 kr") plus ``requiredProductQuantity``. The per-unit offer therefore comes
from parsing the store's own label, under the v0.41.2 campaign doctrine: the parsed
quantity must agree with ``requiredProductQuantity``, a label that parses as a SAVING
("Spara 5 kr") or not at all yields NO offer rather than a guess, several parseable
promotions pick the HIGHEST per-unit price (the smaller claimed saving is the safer
error), and an offer at or above ordinarie is refused wholesale (v0.32.1). A label
priced per MEASURE ("109 kr/kg" on a catchweight chicken) is converted to the package
through the store's own ordinarie/jämförpris ratio, or refused when the measures
disagree — recorded raw it would understate a ~925 g package by its weight while
passing every guard.

This extractor makes NO HTTP calls of its own: like JsonLdExtractor it parses the page
the pipeline already fetched, so it needs no ledger slot and can never see a bot wall.
Any parse failure returns None and the ladder falls through to JSON-LD — right
ordinarie, no offer — and then the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from domain.extractors.base import read_json_object
from domain.quickadd import parse_package_from_name
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r"window\.__QUERY_INITIAL_STATE__\s*=\s*\{")

# The URL's trailing path segment is the retailer product id ("...-p%C3%A5gen/2010293").
_PRODUCT_ID_RE = re.compile(r"/(\d+)/?(?:[?#]|$)")

# "2 för 45 kr" / "3 för 95:-" — the total is what N units cost together.
_MULTI_BUY_RE = re.compile(r"(\d+)\s*för\s*(\d+(?:[.,]\d{1,2})?)\s*(?:kr|:-)?", re.IGNORECASE)

# A bare per-unit price ("Stammispris 20 kr"). The currency marker is required so a
# stray count ("2 st") can never read as a price. "kr/st" counts as bare: styck IS the
# sellable package (verified live: "20 kr/st" on a 200 g tomato tray priced 28.10).
_SINGLE_PRICE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:kr|:-)", re.IGNORECASE)

# "109 kr/kg" — a price per MEASURE, not per package (verified live on a CATCHWEIGHT
# chicken: package ca 925 g, ordinarie 150.91, campaign "109 kr/kg" — recording 109.00
# as the package offer is wrong by the package weight, yet passes the inversion guard).
# Converted to a package price via the store's own ordinarie/jämförpris ratio below.
_PER_MEASURE_RE = re.compile(
    r"(\d+(?:[.,]\d{1,2})?)\s*(?:kr|:-)\s*/\s*(kg|hg|liter|l)\b", re.IGNORECASE
)

# What the state's unitPrice.unit must contain for the label's measure to be the SAME
# measure ("fop.price.per.kg", "fop.price.per.litre.without.deposit").
_MEASURE_UNIT_MARKERS = {"kg": "per.kg", "liter": "per.litre", "l": "per.litre"}

# Labels that state a DISCOUNT AMOUNT or percentage, not a price you pay. "Spara 5 kr"
# through _SINGLE_PRICE_RE would record 5.00 as the offer — plausible-looking (below
# ordinarie) and completely wrong, the exact failure class v0.41.1 removed from Willys.
_SAVING_WORDS_RE = re.compile(r"spara|rabatt|%", re.IGNORECASE)

# A packSizeDescription that is a RANGE ("766.4g - 1000000g", catchweight) names no
# single package amount — the parser would read the lower bound as evidence.
_RANGE_SIZE_RE = re.compile(r"\d\s*[gkl]?[gl]?\s*[-–]\s*\d")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


class IcaExtractor:
    """Extract price data from ICA's ``window.__QUERY_INITIAL_STATE__`` hydration state."""

    CONFIDENCE = 0.98

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        """Parse the hydration state and return a result, or None for the JSON-LD fallback."""
        node = self._find_product(html, store_url)
        if node is None:
            return None

        price = _to_decimal((node.get("price") or {}).get("amount"))
        if price is None or price <= 0:
            logger.debug("ICA state without a usable price")
            return None

        # unitPrice is the PRINTED jämförpris (24.82 + "fop.price.per.kg" renders as
        # "24,82 kr/kg") — displayed beside the computed value, never sorted on. Resolved
        # before the offer because a per-measure campaign label converts through it.
        unit_price = _to_decimal(((node.get("unitPrice") or {}).get("price") or {}).get("amount"))
        store_unit_price_sek = unit_price if unit_price is not None and unit_price > 0 else None
        unit_code = str((node.get("unitPrice") or {}).get("unit") or "")

        offer_price, offer_details = self._best_offer(node, price, store_unit_price_sek, unit_code)
        offer_type: str | None = None
        if offer_price is not None:
            # The v0.32.1 invariant: an "offer" at or above ordinarie is no offer at all —
            # refused together with its details, never recorded as a price cut.
            if offer_price >= price:
                logger.warning(
                    "ICA promotion %r parses to %s >= ordinarie %s - refusing the offer",
                    offer_details,
                    offer_price,
                    price,
                )
                offer_price, offer_details = None, None
            else:
                assert offer_details is not None
                offer_type = "stammispris" if "stammis" in offer_details.lower() else "kampanj"

        in_stock = node.get("available") is not False

        # Package evidence from packSizeDescription ("1.1kg"), read with the same coded
        # parser quick-add uses on titles. A catchweight RANGE ("766.4g - 1000000g")
        # names no single amount — the parser would read the lower bound as evidence, so
        # it yields none at all. Unparseable text likewise yields all-None.
        size_text = str(node.get("packSizeDescription") or "")
        if _RANGE_SIZE_RE.search(size_text):
            size_text = ""
        guess = parse_package_from_name(size_text)

        return PriceExtractionResult(
            price_sek=price,
            store_unit_price_sek=store_unit_price_sek,
            offer_price_sek=offer_price,
            offer_type=offer_type,
            offer_details=offer_details,
            in_stock=in_stock,
            confidence=self.CONFIDENCE,
            pack_size=guess.pack_size,
            package_amount=guess.amount,
            package_unit=guess.entry_unit,
            raw_response={
                "source": "ica_page",
                "price": float(price),
                "offer_price": float(offer_price) if offer_price is not None else None,
                "offer_details": offer_details,
                "unit_price": float(unit_price) if unit_price is not None else None,
                "unit_price_unit": ((node.get("unitPrice") or {}).get("unit")),
            },
        )

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None:
        """Deliberately None: ICA's JSON-LD already carries name, brand and size, so the
        quick-add preview's JSON-LD tier answers everything the form wants prefilled."""
        return None

    def _best_offer(
        self,
        node: dict[str, Any],
        price: Decimal,
        unit_price: Decimal | None,
        unit_code: str,
    ) -> tuple[Decimal | None, str | None]:
        """The per-package campaign price parsed from the store's own promotion labels.

        When several promotions parse, the HIGHEST price wins — the ranking runs on what
        you pay either way, and the smaller claimed saving is the safer error (Lyko's
        rule). An unparseable or contradictory promotion contributes nothing.
        """
        promotions = node.get("promotions")
        if not isinstance(promotions, list):
            return None, None
        candidates: list[tuple[Decimal, str]] = []
        for promo in promotions:
            if not isinstance(promo, dict):
                continue
            description = str(promo.get("description") or "").strip()
            parsed = self._parse_promotion(
                description, promo.get("requiredProductQuantity"), price, unit_price, unit_code
            )
            if parsed is not None:
                candidates.append((parsed, description))
            elif description:
                logger.warning(
                    "ICA promotion %r did not parse to a package price - recording no offer for it",
                    description,
                )
        if not candidates:
            return None, None
        return max(candidates, key=lambda item: item[0])

    def _parse_promotion(
        self,
        description: str,
        required_quantity: Any,
        price: Decimal,
        unit_price: Decimal | None,
        unit_code: str,
    ) -> Decimal | None:
        """Package price from one promotion label, or None when it cannot be trusted.

        ICA promotions carry no price field, so the label is the only source. A multi-buy
        label must agree with ``requiredProductQuantity``; a multi-buy quantity with no
        multi-buy label gets NO price (the total is unknowable) — never arithmetic. A
        label priced per MEASURE ("109 kr/kg") is converted to the package through the
        store's own ordinarie/jämförpris ratio — the same arithmetic ICA uses to print
        the ca-price on a catchweight package — and only when the label's measure IS the
        jämförpris measure; "kr/st" needs no conversion, styck is the package.
        """
        if not description:
            return None
        quantity = required_quantity if isinstance(required_quantity, int) else None

        measure = _PER_MEASURE_RE.search(description)
        multi = _MULTI_BUY_RE.search(description)
        if measure is not None:
            if multi is not None or (quantity is not None and quantity > 1):
                # "2 för 109 kr/kg" mixes bases, and a min-quantity condition on a
                # per-measure price cannot ride along visibly — refuse, never guess.
                return None
            per_measure = _to_decimal(measure.group(1))
            marker = _MEASURE_UNIT_MARKERS.get(measure.group(2).lower())
            if per_measure is None or per_measure <= 0 or marker is None:
                return None
            if unit_price is None or marker not in unit_code:
                logger.warning(
                    "ICA promotion %r is priced per measure but the page's jämförpris "
                    "unit is %r - refusing the conversion",
                    description,
                    unit_code or None,
                )
                return None
            return (per_measure * price / unit_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if multi:
            label_quantity = int(multi.group(1))
            total = _to_decimal(multi.group(2))
            if label_quantity < 1 or total is None or total <= 0:
                return None
            if quantity is not None and quantity > 0 and quantity != label_quantity:
                logger.warning(
                    "ICA promotion %r says %d units but requiredProductQuantity is %s - "
                    "refusing the contradiction",
                    description,
                    label_quantity,
                    quantity,
                )
                return None
            # "3 för 95" is 31.666… — quantize at the boundary, half-up like everywhere else.
            return (total / label_quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if _SAVING_WORDS_RE.search(description):
            return None
        if quantity is not None and quantity > 1:
            return None
        single = _SINGLE_PRICE_RE.search(description)
        if single:
            value = _to_decimal(single.group(1))
            if value is not None and value > 0:
                return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return None

    def _find_product(self, html: str, store_url: str) -> dict[str, Any] | None:
        """The state's product node, verified against the URL's product id.

        The react-query state nests the product under queries[].state.data, and the page
        can carry OTHER products (alternatives, recommendations) — so identity is the
        ``retailerProductId`` matching the URL's trailing digits, and no match means the
        state describes some other product: return None rather than record a foreign
        price at 0.98 confidence.
        """
        id_match = _PRODUCT_ID_RE.search(store_url)
        if not id_match:
            logger.debug("No product id in ICA URL: %s", store_url)
            return None
        product_id = id_match.group(1)

        state_match = _STATE_RE.search(html)
        if not state_match:
            logger.debug("No window.__QUERY_INITIAL_STATE__ in ICA page")
            return None
        raw = read_json_object(html, state_match.end() - 1)
        if raw is None:
            logger.debug("Unbalanced window.__QUERY_INITIAL_STATE__ object")
            return None
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Malformed window.__QUERY_INITIAL_STATE__ JSON")
            return None

        node = self._walk_for_product(state, product_id)
        if node is None:
            logger.warning(
                "ICA state carries no product with retailerProductId %s - ignoring the "
                "state, falling back to JSON-LD",
                product_id,
            )
        return node

    def _walk_for_product(self, value: Any, product_id: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if str(value.get("retailerProductId")) == product_id and isinstance(
                value.get("price"), dict
            ):
                return value
            for child in value.values():
                found = self._walk_for_product(child, product_id)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = self._walk_for_product(child, product_id)
                if found is not None:
                    return found
        return None
