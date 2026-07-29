"""Lyko page-state price extractor.

Lyko (an Avensia Nitro storefront, like Rusta) server-renders both a schema.org JSON-LD
node and a richer ``window.CURRENT_PAGE`` hydration object — and, exactly like Rusta's,
the JSON-LD carries ONLY the current price. Verified live 2026-07-29 on N.C.P. Hand Wash
201 (``.../n.c.p.-olfactives-hand-wash-201-apple---driftwood-300ml``): the JSON-LD says
``"price": "58"`` while the page sells it as 290 kr struck through, 80 % off. Reading the
JSON-LD alone would record 58 as ``price_sek`` — the ordinarie/offer inversion v0.25.2 and
v0.25.3 fixed in the other store families, and with it a permanent 58 kr "ordinarie" that
never surfaces in Erbjudanden. CURRENT_PAGE also carries structured ``size``/``unit``
("500 ml" as 500.0 + "ml"), so the package needs no title parsing at all.

Two shapes differ from Rusta's state in ways that punish a copy-paste:

**``comparisonPrices`` is NOT a jämförpris.** Rusta's ``comparisonPrice`` is kr/liter; Lyko's
``comparisonPrices`` is a LIST of other prices to compare against, each with a ``type``, and
only two of the four observed types are an ordinarie this store actually charged:

    ordinary           "Tid. pris"        290.00  ← ordinarie
    withoutCampaign    "Utan kampanj"     139.00  ← ordinarie
    recommended        "Rek. pris"        129.00  ← the MANUFACTURER's RRP
    packageContentSum  "Utan paketpris:"   98.00  ← a kit's parts bought separately

The last two are excluded on evidence, not taste: across the 24 products on one live
listing, ``ordinary``/``withoutCampaign`` appeared if and only if Lyko itself rendered the
product as discounted (``useDiscountStyling``/``discountPercent`` set), while every
``recommended`` and ``packageContentSum`` sat on an undiscounted product. Treating an RRP as
ordinarie would mark those products as permanently on sale and fill Erbjudanden with deals
that are not deals. Lyko prints no jämförpris anywhere (``unitPrice`` was null on all 24 and
the page carries no "jämförpris" text), so ``store_unit_price_sek`` stays None and the app's
computed kr/unit is the only comparison figure — which is what ``package_quantity`` is for.

**The URL carries no article code.** Rusta anchors identity on a trailing digit run; a Lyko
URL is a pure slug (``/sv/palmolive/palmolive-hand-wash-refill-milk---honey-500-ml``). The
anchor here is the state's own ``canonicalUrl``, compared on the LAST path segment only:
the same product answers on both a brand path and a category path (verified — fetching
``/sv/hudvard/.../handtval/n.c.p.-...-300ml`` returns 200 with ``canonicalUrl`` pointing at
``/sv/n.c.p.-olfactives/...``), so comparing whole paths would refuse a page that is fine.
Size variants each have their own slug and their own price, and the state root IS the
selected variant, so a slug that matches only a sibling in ``variations`` means the state
describes a DIFFERENT package than the link — refused rather than recorded.

Makes NO HTTP calls of its own: like the other store-HTML tiers it parses the page the
pipeline already fetched, so it needs no ledger slot and can never see a bot wall. Any
parse failure returns None and the ladder falls through to JSON-LD (the right price outside
a campaign) and then the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote, urlparse

from domain.extractors.base import read_json_object
from domain.pricing import PKG_UNITS
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

_CURRENT_PAGE_RE = re.compile(r"window\.CURRENT_PAGE\s*=\s*\{")

# comparisonPrices entries that establish an ORDINARIE — a price Lyko itself charged for
# this product. "recommended" (the manufacturer's RRP) and "packageContentSum" (what a
# kit's parts cost bought separately) are deliberately absent; see the module docstring.
_ORDINARIE_TYPES = frozenset({"ordinary", "withoutcampaign"})

# onlineStatus.interactionType values that mean "you cannot buy this right now". Only
# "buy" was observed live, so this is a DENY list with an optimistic default: an unknown
# marker on a buyable product reads as in stock (matching the JSON-LD extractor), rather
# than a new Lyko enum value silently reporting every product as sold out.
_OUT_OF_STOCK_INTERACTIONS = frozenset(
    {"soldout", "outofstock", "notifyme", "notify", "comingsoon", "discontinued", "unavailable"}
)


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


def _slug(url: str | None) -> str | None:
    """The last path segment of a URL, lowercased — Lyko's product identity.

    Query and fragment are dropped by urlparse, and percent-escapes are decoded so a
    pasted ``%C3%A5`` matches the state's plain ``å``.
    """
    if not url:
        return None
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    if not segments:
        return None
    return unquote(segments[-1]).strip().lower() or None


class LykoExtractor:
    """Extract price data from Lyko's server-rendered ``window.CURRENT_PAGE`` state."""

    CONFIDENCE = 0.98

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        """Parse CURRENT_PAGE and return a result, or None for the JSON-LD/LLM fallback."""
        page = self._find_product(html, store_url)
        if page is None:
            return None

        price_obj = page.get("price")
        if not isinstance(price_obj, dict):
            return None
        current = _to_decimal(price_obj.get("current"))
        if current is None or current <= 0:
            logger.debug("Lyko CURRENT_PAGE without a usable current price")
            return None

        # Campaign handling, harmonized with the other store families: price_sek is the
        # ordinarie, offer_price_sek is what you pay now. Outside a campaign Lyko lists no
        # ordinarie comparison at all, so this quietly does nothing.
        price = current
        offer_price_sek: Decimal | None = None
        offer_type: str | None = None
        offer_details: str | None = None
        ordinarie, ordinarie_type = self._ordinarie(price_obj, current)
        if ordinarie is not None:
            price = ordinarie
            offer_price_sek = current
            offer_type = "kampanj"
            offer_details = f"Spara {_format_sek(ordinarie - current)} kr"

        amount, unit, pack_size = self._package(page)

        return PriceExtractionResult(
            price_sek=price,
            # Lyko prints no jämförpris: unitPrice was null on every product observed and
            # the page renders no "Jämförpris" label. The computed kr/unit covers it.
            store_unit_price_sek=None,
            offer_price_sek=offer_price_sek,
            offer_type=offer_type,
            offer_details=offer_details,
            in_stock=self._in_stock(page),
            confidence=self.CONFIDENCE,
            pack_size=pack_size,
            package_amount=amount,
            package_unit=unit,
            raw_response={
                "source": "lyko_page",
                "price": float(price),
                "offer_price": float(offer_price_sek) if offer_price_sek is not None else None,
                "ordinarie_type": ordinarie_type,
                "discount_percent": _as_float(price_obj.get("discountPercent")),
                "is_member_price": bool(price_obj.get("isMemberPrice", False)),
                "code": str(page.get("code")) if page.get("code") else None,
            },
        )

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None:
        """Identity + package for quick-add, from the same CURRENT_PAGE state.

        ``entryName`` is the abstract good with the brand AND the size already removed
        ("Hand Wash Refill Milk & Honey" where displayName is "Palmolive Hand Wash Refill
        Milk & Honey 500 ml") — exactly the shape the product name wants, so quick-add's
        preview arrives prefilled without a strip_brand_from_name round.
        """
        page = self._find_product(html, store_url)
        if page is None:
            return None

        name = str(page.get("entryName") or page.get("displayName") or "").strip() or None
        if name is None:
            return None

        price_obj = page.get("price") if isinstance(page.get("price"), dict) else {}
        amount, unit, pack_size = self._package(page)

        return ProductMetadata(
            name=name,
            brand=str(page.get("brandName") or "").strip() or None,
            # Lyko's taxonomy is a beauty tree ("Hudvård > Kropp > Handvård & Fotvård"),
            # not this app's grocery sections — leave the choice to the user, as for Rusta.
            category=None,
            price_sek=_to_decimal(price_obj.get("current")),  # preview only: what you pay today
            package_amount=amount,
            package_unit=unit,
            pack_size=pack_size,
            confidence=self.CONFIDENCE,
            source="lyko_page",
            in_stock=self._in_stock(page),
        )

    @staticmethod
    def _ordinarie(
        price_obj: dict[str, Any], current: Decimal
    ) -> tuple[Decimal | None, str | None]:
        """The ordinarie this campaign discounts, from ``comparisonPrices``, or (None, None).

        Only the types Lyko itself charged count (``_ORDINARIE_TYPES``), and only above the
        current price — an "ordinarie" at or below what you pay is not a campaign, and
        letting it through would invert the offer the way v0.32.1 forbade for the LLM.

        When several qualify, the LOWEST wins: a smaller claimed saving is the safer error,
        since the ranking runs on effective_price (what you pay) either way and the only
        thing an inflated ordinarie changes is how good the deal is made to look.
        """
        candidates: list[tuple[Decimal, str]] = []
        entries = price_obj.get("comparisonPrices")
        if not isinstance(entries, list):
            return None, None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_type = str(entry.get("type") or "").strip()
            if entry_type.lower() not in _ORDINARIE_TYPES:
                continue
            value = _to_decimal(entry.get("value"))
            if value is not None and value > current:
                candidates.append((value, entry_type))
        if not candidates:
            return None, None
        return min(candidates, key=lambda candidate: candidate[0])

    @staticmethod
    def _package(page: dict[str, Any]) -> tuple[Decimal | None, str | None, int | None]:
        """(amount, entry unit, pack size) from the state's structured ``size``/``unit``.

        No fallback to parsing the title, deliberately: a gift set is named "Hand Soap
        Bloomy Bliss 300 ml & Hand Soap Magnetic Mango 300 ml" and carries size=None, so a
        title parse would read 300 ml for 600 ml of product and quietly halve its kr/liter.
        No evidence beats wrong evidence — the operator types the amount instead.
        """
        unit = str(page.get("unit") or "").strip().lower()
        amount = _to_decimal(page.get("size"))
        if not unit or unit not in PKG_UNITS or amount is None or amount <= 0:
            return None, None, None
        # "st" is an item count, so it fills pack_size too — mirroring how
        # quickadd.parse_package_from_name reads "10 st" off a title.
        pack_size = int(amount) if unit == "st" and amount == amount.to_integral_value() else None
        return amount, unit, pack_size

    @staticmethod
    def _in_stock(page: dict[str, Any]) -> bool:
        """Whether the product is buyable ONLINE.

        Reads the schema.org availability Lyko embeds in the state's own ``linkingData``
        (a standard vocabulary beats guessing at an internal enum) and falls back to
        ``onlineStatus.interactionType``. ``variantStoreAvailabilityStatus`` is deliberately
        NOT consulted: it reported "outOfStockInAllStores" for a product that was in stock
        and on sale online — it describes the physical shops, which this tracker never buys
        from, and using it would mark most of the assortment as unavailable.
        """
        linking_data = page.get("linkingData")
        offers = linking_data.get("offers") if isinstance(linking_data, dict) else None
        if isinstance(offers, list):
            offers = next((offer for offer in offers if isinstance(offer, dict)), None)
        if isinstance(offers, dict):
            availability = offers.get("availability")
            if isinstance(availability, str) and availability.strip():
                # The same marker set as JsonLdExtractor — schema.org spells "not
                # buyable" as OutOfStock, SoldOut OR Discontinued, and recognizing
                # only the first kept a sold-out product rendering as i lager
                # (this tier outranks JSON-LD, so the stricter check never ran).
                normalized = availability.lower().replace("_", "")
                return not any(
                    marker in normalized for marker in ("outofstock", "soldout", "discontinued")
                )

        online_status = page.get("onlineStatus")
        if isinstance(online_status, dict):
            interaction = str(online_status.get("interactionType") or "").strip().lower()
            if interaction:
                return interaction not in _OUT_OF_STOCK_INTERACTIONS
        return True

    def _find_product(self, html: str, store_url: str) -> dict[str, Any] | None:
        """The CURRENT_PAGE state, verified to describe the product the URL names.

        The state root is the SELECTED variant (its ``code`` is the variant's), so the
        check is its ``canonicalUrl`` slug against the requested one. A slug that instead
        matches an entry in ``variations``/``variants`` means Lyko served a sibling package
        — a different size at a different price — and recording that at 0.98 confidence
        would attach another product's price to this link.
        """
        wanted = _slug(store_url)
        if wanted is None:
            logger.debug("No product slug in Lyko URL: %s", store_url)
            return None

        script_match = _CURRENT_PAGE_RE.search(html)
        if not script_match:
            logger.debug("No window.CURRENT_PAGE in Lyko page")
            return None
        raw = read_json_object(html, script_match.end() - 1)
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

        canonical = _slug(str(page.get("canonicalUrl") or "")) or _slug(str(page.get("url") or ""))
        if canonical == wanted:
            return page

        for key in ("variations", "variants"):
            siblings = page.get(key)
            if not isinstance(siblings, list):
                continue
            for sibling in siblings:
                if isinstance(sibling, dict) and _slug(str(sibling.get("url") or "")) == wanted:
                    logger.warning(
                        "Lyko served variant %r while the link asks for %r - the state "
                        "prices a different package; falling back to JSON-LD",
                        canonical,
                        wanted,
                    )
                    return None

        logger.warning(
            "Lyko CURRENT_PAGE canonical slug %r does not match the URL %r - ignoring the "
            "state, falling back to JSON-LD",
            canonical,
            wanted,
        )
        return None


def _as_float(value: Any) -> float | None:
    """A raw_response-safe float (the dict is typed str|float|bool|None), or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
