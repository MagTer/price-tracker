"""Generic schema.org JSON-LD price extractor.

Swedish e-commerce sites embed schema.org Product data in server-rendered
``<script type="application/ld+json">`` blocks (verified 2026-07-13 against
live product pages: ICA handlaprivatkund, Apotea, Med24, DOZ Apotek;
2026-07-22: Kronans Apotek).
Parsing that is exact and free, and reuses the page fetch the pipeline
already made — so it sits between the store-API extractors and the LLM
cascade in ``PriceParser.extract_price``.

Shapes handled (all observed in the wild):
- top-level ``Product`` object (ICA, Apotea)
- ``ItemPage``/``WebPage`` wrapper with ``mainEntity: Product`` (DOZ)
- lists of typed objects and ``@graph`` arrays (Med24, Kronans Apotek)
- ``price`` as JSON number or string, with either dot or comma decimals
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any

from domain.result import PriceExtractionResult

logger = logging.getLogger(__name__)

_LDJSON_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_OUT_OF_STOCK_MARKERS = ("OutOfStock", "SoldOut", "Discontinued")

# Tokens of 3+ alphanumerics (Swedish letters included). Shorter fragments ("3",
# "p", "st") appear in virtually every product title and would fake an overlap.
_NAME_TOKEN_RE = re.compile(r"[a-z0-9åäö]{3,}")


class JsonLdExtractor:
    """Extract price data from schema.org Product JSON-LD in raw HTML."""

    CONFIDENCE = 0.95

    def extract_from_html(
        self, html: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        """Parse JSON-LD blocks and return a result, or None for LLM fallback."""
        product = self._find_product(html)
        if product is None:
            return None

        # Name sanity: a recommendation carousel can put ITS Product node first in the
        # markup, and recording that price with 0.95 confidence poisons the history.
        # Zero token overlap between the tracked name and the node's name means the
        # node is (almost certainly) some other product — fall through to the LLM
        # rather than trying subsequent Product nodes (locked decision).
        name = unescape(str(product.get("name", "")))
        if product_name:
            expected = set(_NAME_TOKEN_RE.findall(product_name.lower()))
            found = set(_NAME_TOKEN_RE.findall(name.lower()))
            if expected and found and not (expected & found):
                logger.warning(
                    "JSON-LD Product name %r shares no token with tracked product %r "
                    "- ignoring the node, falling back to LLM",
                    name,
                    product_name,
                )
                return None

        offer = self._first_offer(product.get("offers"))
        if offer is None:
            logger.debug("JSON-LD Product without usable offers")
            return None

        currency = str(offer.get("priceCurrency", "")).upper()
        if currency and currency != "SEK":
            logger.warning("JSON-LD offer in unexpected currency: %s", currency)
            return None

        price = self._parse_price(offer.get("price") or offer.get("lowPrice"))
        if price is None:
            return None

        in_stock = self._parse_availability(offer.get("availability"))

        return PriceExtractionResult(
            price_sek=price,
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=in_stock,
            confidence=self.CONFIDENCE,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={
                "source": "jsonld",
                "name": name,
                "price": float(price),
                "currency": currency or "SEK",
                "in_stock": in_stock,
            },
        )

    def extract_product_metadata(self, html: str) -> dict[str, str | None] | None:
        """Name and brand of the page's Product node, for quick-add previews.

        No name-overlap sanity check here — quick-add has no tracked name yet to compare
        against, which is exactly why its UI is a preview-and-confirm step rather than a
        blind write: the human is the carousel guard.
        """
        product = self._find_product(html)
        if product is None:
            return None

        name = unescape(str(product.get("name") or "")).strip() or None

        # schema.org allows brand as a plain string or as a Brand/Organization node.
        brand_node = product.get("brand")
        if isinstance(brand_node, dict):
            brand_node = brand_node.get("name")
        brand = unescape(str(brand_node)).strip() if brand_node else None

        if name is None and brand is None:
            return None
        return {"name": name, "brand": brand}

    def _find_product(self, html: str) -> dict[str, Any] | None:
        """Return the first schema.org Product node found in any JSON-LD block."""
        for match in _LDJSON_RE.finditer(html):
            raw = match.group(1).strip()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                logger.debug("Skipping malformed JSON-LD block")
                continue
            product = self._find_product_node(data)
            if product is not None:
                return product
        return None

    def _find_product_node(self, node: Any, depth: int = 0) -> dict[str, Any] | None:
        """Recursively search a parsed JSON-LD structure for a Product node."""
        if depth > 4:
            return None
        if isinstance(node, list):
            for item in node:
                found = self._find_product_node(item, depth + 1)
                if found is not None:
                    return found
            return None
        if not isinstance(node, dict):
            return None

        if self._is_type(node, "Product"):
            return node

        for wrapper_key in ("mainEntity", "@graph"):
            if wrapper_key in node:
                found = self._find_product_node(node[wrapper_key], depth + 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _is_type(node: dict[str, Any], type_name: str) -> bool:
        node_type = node.get("@type")
        if isinstance(node_type, list):
            return type_name in node_type
        return node_type == type_name

    def _first_offer(self, offers: Any) -> dict[str, Any] | None:
        """Return the first Offer/AggregateOffer dict from an offers value."""
        if isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict):
                    return offer
            return None
        if isinstance(offers, dict):
            return offers
        return None

    @staticmethod
    def _parse_price(value: Any) -> Decimal | None:
        """Parse a JSON-LD price (number, or string with dot/comma decimals)."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            price = Decimal(str(value))
        else:
            cleaned = str(value).replace("kr", "").replace(",", ".").strip()
            try:
                price = Decimal(cleaned)
            except InvalidOperation:
                logger.debug("Could not parse JSON-LD price: %r", value)
                return None
        if price <= 0:
            return None
        return price

    @staticmethod
    def _parse_availability(value: Any) -> bool:
        """Map schema.org availability URI to in_stock (missing -> True)."""
        if not value:
            return True
        text = str(value)
        return not any(marker in text for marker in _OUT_OF_STOCK_MARKERS)
