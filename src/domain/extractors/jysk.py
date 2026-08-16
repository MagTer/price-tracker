"""JYSK variant resolver + price-markup extractor.

JYSK (Drupal + React SSR) server-renders schema.org JSON-LD, but that node fails the
tracker in TWO independent ways — both verified live 2026-08-16 and both silent:

1. **A multi-variant product publishes ``ProductGroup``, not ``Product``.** The 24 towel
   variants of NORA hang off ``hasVariant``, and the generic JsonLdExtractor walks only
   ``mainEntity``/``@graph`` — so it returns None and the whole page falls to the LLM.
2. **The JSON-LD price is what one piece costs TODAY, never the ordinarie.** At a rea
   (Badrumsmatta SANDHEM: JSON-LD 75 while the page prints "Ordinarie pris: 149:- /st."
   under a -50 % sticker) recording it as ``price_sek`` is the ordinarie/offer inversion
   v0.25.2/v0.32.1 exist to prevent — it hides the campaign AND drops a campaign price
   into the 84-day floor as if it were a normal shelf price.

So this tier owns identity as well as the campaign. The page's own price block carries
all three shapes JYSK prints:

    <div class="product-price text-bold">                       <!-- plain -->
      <span class="product-price-value">1299:-</span>

    <div class="product-price discountprice text-bold">         <!-- rea -->
      <span class="product-price-value">75:-</span>
    <div class="product-price-cheapest-price-notice">
      <div>Lägsta pris 30 dagar: 149:- /st. (-50%)</div>
      <div>Ordinarie pris: 149:- /st. </div>

    <div class="product-price text-bold">                       <!-- flerköp -->
      <span class="product-price-value">2 för 369:-</span>
    <span class="... singlepieceprice">299:- /st.</span>

A flerköp is a LABEL, not a price field — the same doctrine as ICA's promotions: the
per-unit offer is parsed from the store's own words ("2 för 369:-" over the 299:- piece
price), quantized to öre, and refused outright when it does not come out below what one
piece costs. Makes NO HTTP calls of its own; every failure returns None and the ladder
continues to plain JSON-LD (right price outside a campaign, on single-variant pages).
"""

from __future__ import annotations

import json
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import unescape
from typing import Any

from domain.extractors.base import LDJSON_BLOCK_RE, format_sek
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

# The variant the URL names: "?article=2342607". JYSK's own variant links use a second
# form ("/articlelookup?article=N") that 302s to the canonical path carrying the same
# parameter, so the query string is the identity either way.
_ARTICLE_RE = re.compile(r"[?&]article=(\d+)")

# The PDP price block. Anchored on the container so a recommendation carousel's teaser
# prices (rendered client-side into "product-teaser-price-wrapper skeleton-content")
# can never be read as the product's own price.
_PRICE_BLOCK_RE = re.compile(
    r'class="pdp-product-price"[^>]*>([\s\S]{0,4000}?)(?:<hr|delivery-selector-container)'
)
_PRICE_VALUE_RE = re.compile(r'class="product-price-value"[^>]*>([\s\S]{0,120}?)</span>')
_SINGLE_PIECE_RE = re.compile(r'class="[^"]*singlepieceprice[^"]*"[^>]*>([\s\S]{0,120}?)</span>')
# "Ordinarie pris:<!-- --> <!-- -->149:- /st. </div>" — the struck-through price, printed
# beside the EU-Omnibus "Lägsta pris 30 dagar" line. Only "Ordinarie pris" is the price
# the store charged outside the campaign; the 30-day low is a legal disclosure about a
# window, and reading it as ordinarie would invent a campaign whenever the two differ.
_ORDINARIE_RE = re.compile(r"Ordinarie pris:([\s\S]{0,120}?)</div>")

# "2 för 369:-" / "3 för 95 kr" — the total N units cost together. The currency marker is
# required for the same reason ICA's copy requires it: "3 för 2" (three for the price of
# two, no total stated) would otherwise parse as a 0.67 kr offer and become the floor.
_MULTI_BUY_RE = re.compile(r"(\d+)\s*för\s*(\d+(?:[\s  ]?\d{3})*(?:[.,]\d{1,2})?)\s*(?:kr|:-)")

# A bare price label: "299:-", "79,95", "1 299:-". The trailing "/st." is stripped by the
# capture bounds, not matched here.
_BARE_PRICE_RE = re.compile(r"(\d+(?:[\s  ]?\d{3})*(?:[.,]\d{1,2})?)\s*(?::-|kr)?")

_ORE = Decimal("0.01")


def _text(fragment: str | None) -> str:
    """Markup fragment as plain text: React's ``<!-- -->`` separators and tags removed.

    The comment separators sit BETWEEN a label and its value ("Ordinarie pris:<!-- -->
    <!-- -->149:-"), so a regex reading the raw fragment finds the digits behind two
    comments and a number parser stops at the '<'.
    """
    if not fragment:
        return ""
    cleaned = re.sub(r"<!--.*?-->", "", fragment, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", unescape(cleaned)).strip()


def _to_decimal(raw: str | None) -> Decimal | None:
    """A Swedish price label as Decimal ("1 299:-" → 1299, "79,95" → 79.95)."""
    if not raw:
        return None
    cleaned = re.sub(r"[\s  ]", "", raw).replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value > 0 else None


class JyskExtractor:
    """Resolve the URL's variant out of the ProductGroup, then add the campaign markup."""

    CONFIDENCE = 0.97

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        found = self._find_variant(html, store_url)
        if found is None:
            return None
        variant, _brand, anchored = found

        piece_price = self._offer_price(variant)
        if piece_price is None:
            logger.debug("JYSK variant %s carries no usable offer price", variant.get("sku"))
            return None

        block = self._price_block(html)
        printed = self._printed_piece_price(block)

        # Identity needs ONE strong signal. The article anchor is one; the page's own
        # printed piece price agreeing with the variant's is the other. With neither —
        # a bare path (so hasVariant order is trusted) whose price block we could not
        # read — a wrong price would ride at 0.97 confidence, so refuse instead.
        if printed is not None and printed != piece_price:
            logger.warning(
                "JYSK printed piece price (%s) disagrees with JSON-LD variant %s (%s) for %s "
                "- ignoring the page tier, falling back to JSON-LD",
                printed,
                variant.get("sku"),
                piece_price,
                store_url,
            )
            return None
        if not anchored and printed is None:
            logger.warning(
                "JYSK page %s carries neither an ?article= anchor nor a readable price "
                "block - refusing to guess which variant it shows",
                store_url,
            )
            return None

        regular = piece_price
        candidates: list[tuple[Decimal, str]] = []

        ordinarie = self._price_from(_ORDINARIE_RE, block)
        if ordinarie is not None and ordinarie > piece_price:
            regular = ordinarie
            candidates.append((piece_price, f"Spara {format_sek(ordinarie - piece_price)} kr"))

        multi = self._multi_buy(block)
        if multi is not None:
            per_unit, label = multi
            candidates.append((per_unit, label))

        # v0.32.1: an offer is what you PAY, below the ordinarie by definition. Anything
        # at or above it is refused wholesale — price, type and details together.
        # Several campaigns on one product is unobserved at JYSK; if it happens, the
        # HIGHEST per-unit wins, because a smaller claimed saving is the safer error
        # (the same rule ICA's several-promotions branch and Lyko's ordinarie pick use).
        priced = [c for c in candidates if c[0] < regular]
        offer_price, offer_details = max(priced, key=lambda c: c[0]) if priced else (None, None)

        return PriceExtractionResult(
            price_sek=regular,
            # JYSK prints no jämförpris anywhere — only "/st." beside the price — so the
            # computed kr/enhet is the only comparison figure, exactly like Lyko.
            store_unit_price_sek=None,
            offer_price_sek=offer_price,
            offer_type="kampanj" if offer_price is not None else None,
            offer_details=offer_details,
            # JYSK publishes NO server-side stock signal: no schema.org availability on any
            # variant, and the buy button is client-rendered. drupal-settings' productStatus
            # is a LIFECYCLE code, not stock — the SANDHEM rea page read "21" (discontinued)
            # while selling fine — so reading it as availability would be Lyko's
            # variantStoreAvailabilityStatus trap again. True matches what the JSON-LD tier
            # assumes for a missing availability; it is an absence, not an observation.
            in_stock=True,
            confidence=self.CONFIDENCE,
            # Deliberately no package data: JYSK's `size`/`hasMeasurement` are DIMENSIONS
            # ("100x150" cm) and its `weight` is a fabric density (600 g/m²). Neither is an
            # amount of product, and parsing one would put 100 cm on a towel sold per styck.
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={
                "source": "jysk_page",
                "name": str(variant.get("name") or "") or None,
                "article": str(variant.get("sku") or "") or None,
                "price": float(regular),
                "offer_price": float(offer_price) if offer_price is not None else None,
                "currency": "SEK",
                "in_stock": True,
            },
        )

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None:
        """Identity for quick-add, which the JSON-LD tier cannot supply on a ProductGroup.

        Only the URL's own variant carries a full name ("Badlakan NORA 100x150 sand"); its
        siblings are named bare after the group ("NORA"), so previewing the group node
        would offer a name that names no purchasable article.
        """
        found = self._find_variant(html, store_url)
        if found is None:
            return None
        variant, brand, _anchored = found

        name = str(variant.get("name") or "").strip() or None
        if name is None:
            return None

        return ProductMetadata(
            name=name,
            brand=brand,
            category=None,  # JYSK's breadcrumbs are furniture sections, not the app's taxonomy.
            price_sek=self._offer_price(variant),  # preview display only — what you pay today
            package_amount=None,  # see extract_from_html: JYSK sizes are dimensions.
            package_unit=None,
            pack_size=None,
            confidence=self.CONFIDENCE,
            source="jysk_page",
            in_stock=None,  # no server-side signal to report
        )

    def _find_variant(self, html: str, store_url: str) -> tuple[dict, str | None, bool] | None:
        """The variant the URL names, its group brand, and whether an ?article= anchored it."""
        node = self._find_node(html)
        if node is None:
            logger.debug("No JYSK Product/ProductGroup JSON-LD in page")
            return None

        brand = node.get("brand")
        brand_name = None
        if isinstance(brand, dict):
            brand_name = str(brand.get("name") or "").strip() or None

        article_match = _ARTICLE_RE.search(store_url)
        article = article_match.group(1) if article_match else None

        variants = node.get("hasVariant")
        if isinstance(variants, list) and variants:
            if article is not None:
                for variant in variants:
                    if isinstance(variant, dict) and str(variant.get("sku") or "") == article:
                        return variant, brand_name, True
                # The page carries a group that does not contain the article the URL names:
                # the product moved or the URL is stale. Recording a sibling's price would
                # be a foreign product at 0.97 confidence.
                logger.warning(
                    "JYSK article %s is not among the %d variants on %s - refusing the page",
                    article,
                    len(variants),
                    store_url,
                )
                return None
            # No ?article=: the PATH selects the variant, and the selected one is published
            # first (measured on both a bare path and an ?article= URL, 2026-08-16). The
            # printed-price cross-check in extract_from_html is what proves the pick.
            first = variants[0]
            return (first, brand_name, False) if isinstance(first, dict) else None

        # A single-variant product publishes a plain Product node.
        sku = str(node.get("sku") or "")
        if article is not None and sku and sku != article:
            logger.warning(
                "JYSK page %s publishes article %s, not the %s the URL names - refusing",
                store_url,
                sku,
                article,
            )
            return None
        return node, brand_name, article is not None and sku == article

    def _find_node(self, html: str) -> dict[str, Any] | None:
        """The page's ProductGroup (multi-variant) or Product (single) JSON-LD node."""
        for match in LDJSON_BLOCK_RE.finditer(html):
            try:
                data = json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                logger.debug("Skipping malformed JSON-LD block")
                continue
            node = self._walk(data)
            if node is not None:
                return node
        return None

    def _walk(self, node: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 4:
            return None
        if isinstance(node, list):
            for item in node:
                found = self._walk(item, depth + 1)
                if found is not None:
                    return found
            return None
        if not isinstance(node, dict):
            return None
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        # ProductGroup first: on a multi-variant page BOTH types are present (the group
        # wraps Product variants), and the group is what carries the brand and the list.
        if "ProductGroup" in types or "Product" in types:
            return node
        for wrapper in ("mainEntity", "@graph"):
            if wrapper in node:
                found = self._walk(node[wrapper], depth + 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _offer_price(variant: dict[str, Any]) -> Decimal | None:
        """The variant's piece price — what ONE of them costs today."""
        offers = variant.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if not isinstance(offers, dict):
            return None
        currency = str(offers.get("priceCurrency", "")).upper()
        if currency and currency != "SEK":
            logger.warning("JYSK offer in unexpected currency: %s", currency)
            return None
        return _to_decimal(str(offers.get("price")) if offers.get("price") is not None else None)

    @staticmethod
    def _price_block(html: str) -> str:
        match = _PRICE_BLOCK_RE.search(html)
        return match.group(1) if match else ""

    def _printed_piece_price(self, block: str) -> Decimal | None:
        """What the page itself says one piece costs, or None when it states no plain price.

        With a flerköp running, ``product-price-value`` holds the LABEL ("2 för 369:-") and
        the piece price moves to ``singlepieceprice`` — so the piece price is read from the
        support line first, and the headline value is used only when it parses as a bare
        price on its own.
        """
        single = self._price_from(_SINGLE_PIECE_RE, block)
        if single is not None:
            return single
        headline = _text(self._first(_PRICE_VALUE_RE, block))
        if not headline or _MULTI_BUY_RE.search(headline):
            return None
        return _to_decimal(self._first(_BARE_PRICE_RE, headline))

    def _multi_buy(self, block: str) -> tuple[Decimal, str] | None:
        """A "N för X" headline as a per-unit offer plus the store's own words as villkor."""
        headline = _text(self._first(_PRICE_VALUE_RE, block))
        match = _MULTI_BUY_RE.search(headline)
        if match is None:
            return None
        quantity = int(match.group(1))
        total = _to_decimal(match.group(2))
        if quantity < 1 or total is None:
            return None
        # "3 för 95" is 31.666… — quantize at the boundary, half-up like everywhere else.
        # offer_details carries the label VERBATIM: the price only exists on condition that
        # you buy N, and shortening that to a badge drops which condition applies (v0.41.2).
        return (total / quantity).quantize(_ORE, rounding=ROUND_HALF_UP), headline

    def _price_from(self, pattern: re.Pattern[str], block: str) -> Decimal | None:
        """The first bare price inside the fragment ``pattern`` captures, or None."""
        return _to_decimal(self._first(_BARE_PRICE_RE, _text(self._first(pattern, block))))

    @staticmethod
    def _first(pattern: re.Pattern[str], text: str | None) -> str | None:
        if not text:
            return None
        match = pattern.search(text)
        return match.group(1) if match else None
