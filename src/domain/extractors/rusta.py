"""Rusta page-state price extractor.

Rusta (an Avensia Nitro storefront) server-renders TWO structured views of a product:
a schema.org JSON-LD node and a richer ``window.CURRENT_PAGE`` hydration object. The
JSON-LD carries only the CURRENT price — at a sale (verified live 2026-07-27, Hängstol
Sorrento: current 1899.00, original 2999.00) the ordinarie exists ONLY in CURRENT_PAGE,
so extracting from JSON-LD alone records the campaign price as ``price_sek``: exactly
the ordinarie/offer inversion v0.25.2/v0.25.3 fixed in the other store families.
CURRENT_PAGE also carries the printed jämförpris (``comparisonPrice``/``comparisonUnit``
— absent from the JSON-LD entirely) and the stock level, and its ``subTitle`` holds the
package text ("50 ml Invisible Black & White") that Rusta's bare JSON-LD names drop.

This extractor makes NO HTTP calls of its own: like JsonLdExtractor it parses the page
the pipeline already fetched, so it needs no ledger slot and can never see a bot wall.
Any parse failure returns None and the ladder falls through to JSON-LD — right price
outside campaigns — and then the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from domain.quickadd import parse_package_from_name
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

_CURRENT_PAGE_RE = re.compile(r"window\.CURRENT_PAGE\s*=\s*\{")

# The trailing digit run of a product URL's path is the article code ("...-morkgra-
# konstrotting-605011720101"). Both 8- and 12-digit codes exist in the wild; 6+ keeps a
# dimension fragment like "105x190" from ever matching (it is never URL-terminal anyway).
_ARTICLE_CODE_RE = re.compile(r"-(\d{6,})/?(?:[?#]|$)")

# CURRENT_PAGE.stock is a coarse level, not a boolean. "high"/"low" observed live;
# anything except an explicit empty marker counts as in stock (missing → True, matching
# the JSON-LD extractor's optimistic default).
_OUT_OF_STOCK_LEVELS = frozenset({"none", "outofstock", "empty"})


def _read_json_object(text: str, start: int) -> str | None:
    """The balanced ``{...}`` starting at ``start``, string-escape aware, or None."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _format_sek(value: Decimal) -> str:
    """A Decimal as Swedish label text: no trailing zeros, no scientific notation."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


class RustaExtractor:
    """Extract price data from Rusta's server-rendered ``window.CURRENT_PAGE`` state."""

    CONFIDENCE = 0.98

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        """Parse CURRENT_PAGE and return a result, or None for the JSON-LD/LLM fallback."""
        found = self._find_product(html, store_url)
        if found is None:
            return None
        page, node = found

        price_obj = node.get("price")
        if not isinstance(price_obj, dict):
            return None
        current = _to_decimal((price_obj.get("current") or {}).get("inclVat"))
        original = _to_decimal((price_obj.get("original") or {}).get("inclVat"))
        if current is None or current <= 0:
            logger.debug("Rusta CURRENT_PAGE without a usable current price")
            return None

        # Campaign handling, harmonized with the other store families: price_sek is the
        # ordinarie, offer_price_sek is what you pay now. Outside a campaign Rusta sets
        # original == current, so the guard below quietly does nothing.
        price = current
        offer_price_sek: Decimal | None = None
        offer_type: str | None = None
        offer_details: str | None = None
        if original is not None and original > current:
            price = original
            offer_price_sek = current
            offer_type = "kampanj"
            offer_details = f"Spara {_format_sek(original - current)} kr"

        # comparisonPrice is the PRINTED jämförpris ("498.0" + "/liter" renders as
        # "Jämförpris 498 kr /liter"); 0.0/null means the page prints none.
        comparison = _to_decimal(price_obj.get("comparisonPrice"))
        store_unit_price_sek = comparison if comparison is not None and comparison > 0 else None

        stock = node.get("stock") or page.get("stock")
        in_stock = str(stock).strip().lower() not in _OUT_OF_STOCK_LEVELS if stock else True

        # Package evidence from the subTitle ("50 ml Invisible Black & White"), read with
        # the same coded parser quick-add uses on titles. A furniture subTitle ("105x190 cm
        # Mörkgrå Konstrotting") has no parseable unit and yields all-None — no bad evidence.
        guess = parse_package_from_name(str(page.get("subTitle") or ""))

        return PriceExtractionResult(
            price_sek=price,
            store_unit_price_sek=store_unit_price_sek,
            offer_price_sek=offer_price_sek,
            offer_type=offer_type,
            offer_details=offer_details,
            in_stock=in_stock,
            confidence=self.CONFIDENCE,
            pack_size=guess.pack_size,
            package_amount=guess.amount,
            package_unit=guess.entry_unit,
            raw_response={
                "source": "rusta_page",
                "price": float(price),
                "offer_price": float(offer_price_sek) if offer_price_sek is not None else None,
                "comparison_unit": price_obj.get("comparisonUnit"),
                "is_sale": bool(page.get("isSale", False)),
                "stock": str(stock) if stock else None,
            },
        )

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None:
        """Identity + package for quick-add, from the same CURRENT_PAGE state.

        Richer than the JSON-LD path on purpose: Rusta's JSON-LD name is bare
        ("Deo roll-on Nivea") while CURRENT_PAGE's subTitle carries the package text
        the preview form wants prefilled.
        """
        found = self._find_product(html, store_url)
        if found is None:
            return None
        page, node = found

        name = str(page.get("displayName") or "").strip() or None
        if name is None:
            return None

        price_obj = node.get("price") if isinstance(node.get("price"), dict) else {}
        current = _to_decimal((price_obj.get("current") or {}).get("inclVat"))

        stock = node.get("stock") or page.get("stock")
        in_stock = str(stock).strip().lower() not in _OUT_OF_STOCK_LEVELS if stock else None

        guess = parse_package_from_name(str(page.get("subTitle") or ""))

        return ProductMetadata(
            name=name,
            brand=str(page.get("brandName") or "").strip() or None,
            category=None,  # Rusta's breadcrumbs aren't the app's taxonomy — leave to the user.
            price_sek=current,  # preview display only — what you pay today
            package_amount=guess.amount,
            package_unit=guess.entry_unit,
            pack_size=guess.pack_size,
            confidence=self.CONFIDENCE,
            source="rusta_page",
            in_stock=in_stock,
        )

    def _find_product(
        self, html: str, store_url: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """(page, price-bearing node) from CURRENT_PAGE, verified against the URL's code.

        The node is the page root when its code matches the URL's article code, or the
        matching entry in ``variations`` (color/size siblings share one page). No match
        means the state describes some OTHER product — return None rather than record a
        wrong price with 0.98 confidence; the URL code is a stronger identity check than
        any name-token overlap.
        """
        code_match = _ARTICLE_CODE_RE.search(store_url)
        if not code_match:
            logger.debug("No article code in Rusta URL: %s", store_url)
            return None
        code = code_match.group(1)

        script_match = _CURRENT_PAGE_RE.search(html)
        if not script_match:
            logger.debug("No window.CURRENT_PAGE in Rusta page")
            return None
        raw = _read_json_object(html, script_match.end() - 1)
        if raw is None:
            logger.debug("Unbalanced window.CURRENT_PAGE object")
            return None
        try:
            page = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Malformed window.CURRENT_PAGE JSON")
            return None
        if not isinstance(page, dict):
            return None

        if str(page.get("code")) == code or str(page.get("variationCode")) == code:
            return page, page

        variations = page.get("variations")
        if isinstance(variations, list):
            for variation in variations:
                if isinstance(variation, dict) and str(variation.get("code")) == code:
                    return page, variation

        logger.warning(
            "Rusta CURRENT_PAGE code %r does not match URL article code %s - ignoring the "
            "state, falling back to JSON-LD",
            page.get("code"),
            code,
        )
        return None
