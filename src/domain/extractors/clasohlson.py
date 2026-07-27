"""Clas Ohlson price-section extractor.

Clas Ohlson (SAP Commerce/hybris) server-renders clean schema.org JSON-LD — but like
Rusta's, it carries only the CURRENT price: at a rea (verified live 2026-07-27, Braun
MultiQuick 44-6051: JSON-LD price 499, page markup ordinarie 999,00) extracting the
JSON-LD alone would record the campaign price as ``price_sek``. And the printed
jämförpris ("(1,91/st)" on consumables) exists only in the markup too. There is no
hydration JSON to read (hybris renders JSP markup, not state), but the price section is
stable BEM markup anchored on the article number:

    <div class="product__normal-price 446051000">
      <span class="product__old-price">999,00</span>          <!-- only at a rea -->
      <span class="product__discount-price">499,00 </span>
      — or, outside a rea —
      <span class="product__price-value">179,90
        <span class="product__comparison-price">(1,91/st)</span></span>

This extractor COMPOSES the shared JsonLdExtractor (identity, availability, the price
authority) and only adds what the markup knows: the ordinarie at a rea and the printed
jämförpris. The markup price must agree with the JSON-LD price or the whole result is
discarded — a mismatch means the anchor found some other product's price block, and a
wrong 0.97-confidence price is worse than falling through. Makes NO HTTP calls of its
own; every failure returns None and the ladder continues to plain JSON-LD.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from decimal import Decimal, InvalidOperation
from html import unescape

from domain.extractors.jsonld import JsonLdExtractor
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

# The /p/ code segment: "Pr446361000", "44-6051", "44-1933-2". Its digits prefix the
# 9-digit SKU that classes the price container ("44-6051" → "446051000").
_URL_CODE_RE = re.compile(r"/p/([A-Za-z0-9-]+)")

_PRICE_BLOCK_RE = re.compile(
    r"product__normal-price\s+(?P<sku>\d+)[^>]*>(?P<block>[\s\S]{0,1500}?)product__vat"
)
_OLD_PRICE_RE = re.compile(r"product__old-price[^>]*>\s*([\d\s .,]+?)\s*<")
_DISCOUNT_PRICE_RE = re.compile(r"product__discount-price[^>]*>\s*([\d\s .,]+?)\s*<")
_PRICE_VALUE_RE = re.compile(r"product__price-value[^>]*>\s*([\d\s .,]+?)\s*<")
# "(1,91/st)" / "(12,50/l)" — the printed jämförpris beside the price value.
_COMPARISON_RE = re.compile(r"product__comparison-price[^>]*>\s*\(?\s*([\d\s .,]+)\s*/")


def _parse_sek(text: str | None) -> Decimal | None:
    """A Swedish price label ("1 290,00", NBSP thousands separator) as Decimal."""
    if not text:
        return None
    cleaned = re.sub(r"[\s ]", "", text).replace(",", ".")
    if not cleaned:
        return None
    try:
        price = Decimal(cleaned)
    except InvalidOperation:
        return None
    return price if price > 0 else None


def _format_sek(value: Decimal) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


class ClasOhlsonExtractor:
    """Enrich the JSON-LD extraction with the price-section markup's rea + jämförpris."""

    CONFIDENCE = 0.97

    def __init__(self) -> None:
        self._jsonld = JsonLdExtractor()

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        block = self._price_block(html, store_url)
        if block is None:
            return None

        old = _parse_sek(self._first(_OLD_PRICE_RE, block))
        discount = _parse_sek(self._first(_DISCOUNT_PRICE_RE, block))
        value = _parse_sek(self._first(_PRICE_VALUE_RE, block))
        comparison = _parse_sek(self._first(_COMPARISON_RE, block))

        current = discount if discount is not None else value
        if current is None:
            return None
        if old is None and comparison is None:
            # The markup knows nothing the JSON-LD does not — let that tier answer.
            return None

        base = self._jsonld.extract_from_html(html, product_name)
        if base is None or base.price_sek is None:
            # No JSON-LD to compose with: identity/availability would be guesses.
            return None
        if base.price_sek != current:
            # The anchored block disagrees with the JSON-LD price — the block belongs to
            # some other product, or the markup changed shape. Fall through rather than
            # record a wrong price at 0.97 confidence.
            logger.warning(
                "Clas Ohlson price section (%s) disagrees with JSON-LD (%s) for %s - "
                "ignoring the markup, falling back to JSON-LD",
                current,
                base.price_sek,
                store_url,
            )
            return None

        updates: dict[str, object] = {
            "confidence": self.CONFIDENCE,
            "raw_response": {
                **(base.raw_response or {}),
                "source": "clasohlson_page",
                "ordinarie": float(old) if old is not None else None,
                "comparison": float(comparison) if comparison is not None else None,
            },
        }
        if old is not None and old > current:
            updates.update(
                price_sek=old,
                offer_price_sek=current,
                offer_type="kampanj",
                offer_details=f"Spara {_format_sek(old - current)} kr",
            )
        if comparison is not None and base.store_unit_price_sek is None:
            updates["store_unit_price_sek"] = comparison

        return dataclasses.replace(base, **updates)

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None:
        """Always None: Clas Ohlson's JSON-LD names are complete ("Yes Platinum All In One
        maskindiskmedel, 94-pack" — package text included), so quick-add's JSON-LD tier
        already previews everything the markup could add."""
        return None

    def _price_block(self, html: str, store_url: str) -> str | None:
        """The product's OWN price block, anchored on the URL's article digits.

        Recommendation carousels carry price markup too — the SKU token in the container
        class ("product__normal-price 446051000") is what proves the block belongs to the
        product the URL names.
        """
        code_match = _URL_CODE_RE.search(store_url)
        if not code_match:
            logger.debug("No /p/ code in Clas Ohlson URL: %s", store_url)
            return None
        digits = re.sub(r"\D", "", code_match.group(1))
        if len(digits) < 6:
            return None

        for match in _PRICE_BLOCK_RE.finditer(html):
            if match.group("sku").startswith(digits):
                # Entity-decode before price parsing: "1&nbsp;290,00" must read as one
                # number, and the char-class regexes stop dead at an '&'.
                return unescape(match.group("block"))
        logger.debug("No price block matching article %s in Clas Ohlson page", digits)
        return None

    @staticmethod
    def _first(pattern: re.Pattern[str], block: str) -> str | None:
        match = pattern.search(block)
        return match.group(1) if match else None
