"""THE butiksblad offer analysis — a pasted blad URL answered with "köp/skippa".

Willys' butiksblad (offline) offers cover in-store-only assortment: the "Läsk 15-pack"
class of offer points at a product with no online page at all, so it can never be a
tracked link (no stable URL, week-scoped, store-scoped — the CLAUDE.md campaign-price
bullet's standing judgement). What CAN be done is answering the question the blad
raises: *is this offer better than the ways I already track of buying the same thing?*

Discovery stays HUMAN by decision (spiked 2026-08-03): the listing API binds its store
to a server session established behind Akamai Bot Manager's JS sensor — a real-browser
problem, same class as ICA's WAF challenge. The per-offer endpoint, by contrast, is
public and sessionless: ``/axfood/rest/v1/offline-product/{storeId}/{code}`` answers
with the campaign price as a NUMBER (``potentialPromotions[].price`` — no label
parsing), ordinarie, package text, jämförpris and conditions. So the operator pastes
the offer URL from the blad, and the analysis is automatic.

The store id is per-instance operator config (env ``WILLYS_OFFLINE_STORE_ID``, default
Willys Sandviken Linggatan — same pattern as QUICKADD_STORE_LABELS): blad offers are
store-scoped, and a second instance shops somewhere else.

Matching against tracked products is token overlap on names/brands, and deliberately
RANKED SUGGESTION, not judgement: "Läsk 15-pack • COCA-COLA ZERO" against a catalogue
of 15 products needs no ML, but a wrong silent match would invent a comparison — so
every candidate is returned with its own numbers and the reader decides. A per-unit
diff is computed only when the offer's printed unit and the product's unit actually
agree (kr/l against kr/kg is the exact nonsense store_unit_price_sek is never sorted
on). Read-only: nothing here writes, nothing schedules.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from domain.models import PricePoint, Product, ProductStore, Store, link_store_name
from domain.result import StoreBlockedError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

OFFLINE_API_BASE = "https://www.willys.se/axfood/rest/v1/offline-product"

# Same ledger key + cadence as WillysApiExtractor: the WAF rate-limits the HOST, and this
# is one more interactive call against it.
_LEDGER_KEY = "host:www.willys.se"
_MIN_INTERVAL_SECONDS = 3.0
_MAX_WAIT_SECONDS = 10.0

# ".../erbjudanden/offline-Lask-15-pack-2500310468" — the trailing digit run is the offer
# code. Bare codes are accepted too (the operator may copy it from anywhere).
_OFFER_CODE_RE = re.compile(r"(\d{6,})/?(?:[?#]|$)")

# "12:08 kr/l +pant" — Willys prints the blad jämförpris with a COLON decimal separator.
_COMPARE_PRICE_RE = re.compile(r"(\d+)[:,.](\d{2})\s*kr/(\w+)(\s*\+\s*pant)?", re.IGNORECASE)

# "15p/33cl" — a multipack volume; also plain "500g" / "1kg" style handled below.
_MULTIPACK_RE = re.compile(r"(\d+)\s*p\s*/\s*(\d+(?:[.,]\d+)?)\s*(cl|ml|dl|l|g|kg)", re.IGNORECASE)
_PLAIN_VOLUME_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(cl|ml|dl|l|g|kg|st|p)$", re.IGNORECASE)

# Entry unit → (canonical product unit, factor). Mirrors pricing.PKG_UNITS' shape but for
# the blad's own vocabulary ("cl", "dl" — never seen in product titles, common in blad).
_VOLUME_UNITS: dict[str, tuple[str, Decimal]] = {
    "ml": ("liter", Decimal("0.001")),
    "cl": ("liter", Decimal("0.01")),
    "dl": ("liter", Decimal("0.1")),
    "l": ("liter", Decimal("1")),
    "g": ("kg", Decimal("0.001")),
    "kg": ("kg", Decimal("1")),
    "st": ("st", Decimal("1")),
    "p": ("st", Decimal("1")),
}

# The printed jämförpris unit → the product unit it compares against.
_COMPARE_UNITS: dict[str, str] = {"l": "liter", "liter": "liter", "kg": "kg", "st": "st"}

# Tokens too generic to identify a product on their own, but kept in scoring when they
# co-occur with a distinctive token ("läsk" ranks the läsk products above the rest).
_TOKEN_RE = re.compile(r"[a-zåäöé0-9]{3,}", re.IGNORECASE)


@dataclass
class BladOffer:
    """One parsed butiksblad offer — the store's own numbers, nothing invented."""

    code: str
    name: str
    brands: list[str]
    ordinarie_price_sek: Decimal | None
    offer_price_sek: Decimal | None
    package_text: str | None
    package_amount: Decimal | None  # in canonical units (liter/kg/st), from package_text
    package_unit: str | None
    unit_price_sek: Decimal | None  # the PRINTED jämförpris ("12:08 kr/l")
    unit_price_unit: str | None  # canonical: liter/kg/st
    excludes_deposit: bool
    conditions: list[str] = field(default_factory=list)
    valid_until: str | None = None


def parse_offer_code(url_or_code: str) -> str | None:
    """The offer code from a blad URL (or a bare code), or None."""
    text = url_or_code.strip()
    match = _OFFER_CODE_RE.search(text)
    return match.group(1) if match else None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _parse_package(text: str | None) -> tuple[Decimal | None, str | None]:
    """('15p/33cl') → (4.95, 'liter'); ('500g') → (0.5, 'kg'); unknown → (None, None)."""
    if not text:
        return None, None
    multi = _MULTIPACK_RE.search(text)
    if multi:
        count = Decimal(multi.group(1))
        each = _to_decimal(multi.group(2))
        spec = _VOLUME_UNITS.get(multi.group(3).lower())
        if each is not None and spec is not None:
            unit, factor = spec
            return count * each * factor, unit
    plain = _PLAIN_VOLUME_RE.match(text.strip())
    if plain:
        amount = _to_decimal(plain.group(1))
        spec = _VOLUME_UNITS.get(plain.group(2).lower())
        if amount is not None and spec is not None:
            unit, factor = spec
            return amount * factor, unit
    return None, None


def parse_offline_offer(code: str, data: dict[str, Any]) -> BladOffer | None:
    """The API payload as a BladOffer, or None when it carries no promotion.

    The campaign price comes from ``potentialPromotions[].price`` — a NUMBER, so unlike
    ICA there is nothing to parse and nothing to guess. Several promotions pick the
    HIGHEST price (smaller claimed saving is the safer error, the standing rule), and
    an offer at or above ordinarie is refused wholesale (v0.32.1) — ordinarie survives.
    """
    promotions = data.get("potentialPromotions")
    if not isinstance(promotions, list) or not promotions:
        return None
    priced = [
        (price, promo)
        for promo in promotions
        if isinstance(promo, dict) and (price := _to_decimal(promo.get("price"))) is not None
    ]
    if not priced:
        logger.warning("Blad offer %s carries no numeric promotion price - refusing", code)
        return None
    offer_price, promo = max(priced, key=lambda pair: pair[0])
    offer_price = offer_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    ordinarie = _to_decimal(data.get("priceNoUnit"))

    unit_price = None
    unit_price_unit = None
    excludes_deposit = False
    compare = _COMPARE_PRICE_RE.search(str(promo.get("comparePrice") or ""))
    if compare:
        unit_price = Decimal(f"{compare.group(1)}.{compare.group(2)}")
        unit_price_unit = _COMPARE_UNITS.get(compare.group(3).lower())
        excludes_deposit = compare.group(4) is not None

    package_text = str(promo.get("weightVolume") or data.get("displayVolume") or "") or None
    package_amount, package_unit = _parse_package(package_text)

    conditions: list[str] = []
    if promo.get("redeemLimitLabel"):
        conditions.append(str(promo["redeemLimitLabel"]))
    qualifying = promo.get("qualifyingCount")
    if isinstance(qualifying, int) and qualifying > 1:
        conditions.append(f"Kräver {qualifying} st")
    if str(promo.get("campaignType") or "").upper() == "LOYALTY":
        conditions.append("Kräver Willys Plus")

    valid_until = None
    raw_valid = promo.get("validUntil")
    if isinstance(raw_valid, int | float) and raw_valid > 0:
        valid_until = datetime.fromtimestamp(raw_valid / 1000, tz=UTC).date().isoformat()

    brands = [str(b) for b in promo.get("brands") or [] if b]
    name = str(promo.get("name") or data.get("name") or "").strip() or f"Erbjudande {code}"

    if ordinarie is not None and offer_price >= ordinarie:
        # The v0.32.1 inversion guard: an "offer" at or above ordinarie is no offer.
        logger.warning(
            "Blad offer %s parses to %s >= ordinarie %s - refusing the offer price",
            code,
            offer_price,
            ordinarie,
        )
        return None

    return BladOffer(
        code=code,
        name=name,
        brands=brands,
        ordinarie_price_sek=ordinarie,
        offer_price_sek=offer_price,
        package_text=package_text,
        package_amount=package_amount,
        package_unit=package_unit,
        unit_price_sek=unit_price,
        unit_price_unit=unit_price_unit,
        excludes_deposit=excludes_deposit,
        conditions=conditions,
        valid_until=valid_until,
    )


def offer_unit_price(offer: BladOffer) -> tuple[Decimal | None, str | None]:
    """The offer's kr/unit: the PRINTED figure first, else computed from the package.

    The printed jämförpris is the store's own arithmetic and wins when present; the
    computed fallback (offer price / package amount) exists because not every blad
    offer prints one. Both bases exclude pant when the store says so — the same basis
    Willys' online prices use, so the comparison against tracked links is like-for-like.
    """
    if offer.unit_price_sek is not None:
        return offer.unit_price_sek, offer.unit_price_unit
    if offer.offer_price_sek is not None and offer.package_amount and offer.package_amount > 0:
        computed = (offer.offer_price_sek / offer.package_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return computed, offer.package_unit
    return None, None


def _tokens(*texts: str | None) -> set[str]:
    return {token.lower() for text in texts if text for token in _TOKEN_RE.findall(text)}


async def match_candidates(
    session: AsyncSession, offer: BladOffer, limit: int = 5
) -> list[dict[str, Any]]:
    """Tracked products ranked by name/brand token overlap with the offer.

    Every candidate carries its current cheapest kr/unit across active links (effective
    price — what you would pay), and a ``unit_price_diff_sek`` ONLY when the offer's
    unit and the product's unit agree. Zero-overlap products are not returned: an empty
    candidate list is honest, a foreign product with a number next to it is not.
    """
    offer_tokens = _tokens(offer.name, offer.package_text, *offer.brands)
    if not offer_tokens:
        return []

    offer_unit, offer_unit_kind = offer_unit_price(offer)

    rows = (
        await session.execute(
            select(Product, ProductStore, Store, PricePoint)
            .join(ProductStore, ProductStore.product_id == Product.id)
            .join(Store, Store.id == ProductStore.store_id)
            .join(PricePoint, PricePoint.product_store_id == ProductStore.id)
            .where(ProductStore.is_active.is_(True))
            .order_by(PricePoint.checked_at.desc())
        )
    ).all()

    # Latest point per link, then cheapest kr/unit per product — in Python, the row
    # count is a household catalogue, not a data set.
    latest_by_link: dict[Any, tuple[Product, ProductStore, Store, PricePoint]] = {}
    for product, link, store, point in rows:
        if link.id not in latest_by_link:
            latest_by_link[link.id] = (product, link, store, point)

    best_by_product: dict[Any, dict[str, Any]] = {}
    for product, link, store, point in latest_by_link.values():
        effective = point.offer_price_sek if point.offer_price_sek is not None else point.price_sek
        unit_price = None
        if effective is not None and link.package_quantity and link.package_quantity > 0:
            unit_price = (effective / link.package_quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        current = best_by_product.get(product.id)
        if current is None or (
            unit_price is not None
            and (current["unit_price"] is None or unit_price < current["unit_price"])
        ):
            best_by_product[product.id] = {
                "product": product,
                "store_name": link_store_name(link, store),
                "unit_price": unit_price,
            }

    candidates = []
    for entry in best_by_product.values():
        product = entry["product"]
        overlap = offer_tokens & _tokens(product.name, product.brand)
        if not overlap:
            continue
        unit_price = entry["unit_price"]
        comparable = (
            offer_unit is not None and unit_price is not None and offer_unit_kind == product.unit
        )
        candidates.append(
            {
                "product_id": str(product.id),
                "product_name": product.name,
                "brand": product.brand,
                "unit": product.unit,
                "store_name": entry["store_name"],
                "current_unit_price_sek": float(unit_price) if unit_price is not None else None,
                "unit_price_diff_sek": (
                    float((offer_unit - unit_price).quantize(Decimal("0.01")))
                    if comparable
                    else None
                ),
                "match_score": len(overlap),
            }
        )
    candidates.sort(key=lambda c: (-c["match_score"], c["product_name"].lower()))
    return candidates[:limit]


async def fetch_offline_offer(store_id: str, code: str) -> dict[str, Any] | None:
    """GET the blad offer JSON — shared fetcher, shared ledger, breaker-visible.

    Same contract as WillysApiExtractor._fetch_product: rides the Chrome-fingerprint
    client and spends a slot on the host ledger; a bot wall raises StoreBlockedError so
    the caller can trip the shared breaker instead of reading a wall as "offer gone".
    """
    from infra.providers import get_fetcher, get_rate_limiter

    await get_rate_limiter().acquire(_LEDGER_KEY, _MIN_INTERVAL_SECONDS, max_wait=_MAX_WAIT_SECONDS)
    url = f"{OFFLINE_API_BASE}/{store_id}/{code}"
    result = await get_fetcher().fetch_json(url, referer="https://www.willys.se/erbjudanden")
    if result.get("blocked"):
        raise StoreBlockedError(f"Willys offline API bot wall: {result.get('error')}")
    if not result.get("ok"):
        logger.warning("Willys offline API request failed for %s: %s", code, result.get("error"))
        return None
    data = result.get("data")
    return data if isinstance(data, dict) else None
