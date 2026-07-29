"""THE definition of a deal and its verdict — one query, one classification, one margin.

A deal is an offer on a store LISTING (a link), not on a product-at-a-store: a 24-pack
and an 8-pack of one product at one store are two links and may each be on offer. The
verdict answers the only question that matters — "is this offer actually the cheapest
way to buy the product?" — judged on the COMPUTED kr/unit against the cheapest current
alternative among the product's OTHER links (any store, any pack size):

- ``best``    — nothing else beats it per unit. The answer to "vad ska jag köpa?".
- ``worse``   — a real campaign, but another link is still cheaper per unit.
- ``unknown`` — no honest comparison exists (no amount on this link, or no other link).
  Rides with ``best`` in every consumer, deliberately: it is not KNOWN to be bad.

The store's discount percentage ranks nothing here — 30 % off a bad price is still a
bad price; it is carried as data because the UI shows it.

This judgement used to live in three places at once — the /deals route in admin.py (the
best-alt query), the portal's ``classifyDeal`` JS (the verdict), and
``service.get_current_deals`` (a second deal query with no best-alt at all) — the exact
Gotcha-4 drift pattern. The copies had already diverged behaviorally: the admin route
deduped offer-bearing points per link by recency, so a link whose LATEST point had no
offer still showed its superseded older offer. The latest-per-link JOIN here (the
service's semantics) makes that impossible — the superseded point is not the link's
latest row, and a latest row without an offer is simply not a deal.

Every consumer resolves through :func:`current_deals` — the /deals endpoint (the portal
groups client-side on the ``verdict`` it returns), MCP's ``find_deals`` via
``service.get_current_deals``, and the weekly summary email. Never write a second deal
query or a second verdict.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from domain.models import PricePoint, Product, ProductStore, Store, link_store_name
from domain.pricing import unit_price_py

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEAL_BEST = "best"
DEAL_WORSE = "worse"
DEAL_UNKNOWN = "unknown"

# Most links are checked WEEKLY (Monday offer-day schedule) — a 24h window showed an
# empty deals page from Tuesday on, twice (Gotcha 4: the duplicated query drifted).
DEALS_WINDOW_DAYS = 7

# An offer seen this long ago may already be over — consumers caveat it ("sett fredag"),
# never hide it. The portal's DEAL_STALE_HOURS in admin.html mirrors this value.
DEAL_STALE_HOURS = 48

_CENT = Decimal("0.01")


def classify_deal(unit_price_sek: float | None, best_alt_unit_price_sek: float | None) -> str:
    """The verdict for one deal. Equality is ``best`` — "lika billigt" is still billigast."""
    if unit_price_sek is None or best_alt_unit_price_sek is None:
        return DEAL_UNKNOWN
    return DEAL_BEST if unit_price_sek <= best_alt_unit_price_sek else DEAL_WORSE


def deal_savings(
    unit_price_sek: float | None, best_alt_unit_price_sek: float | None
) -> float | None:
    """Signed kr/unit against the product's next-best link.

    Positive = this offer wins by that much, negative = it loses by that much. None when
    there is nothing to compare against — never 0.0, which would claim a tie we cannot see.
    """
    if unit_price_sek is None or best_alt_unit_price_sek is None:
        return None
    return best_alt_unit_price_sek - unit_price_sek


@dataclass(frozen=True)
class DealRow:
    """One deal — a link whose latest price point is an offer — with its verdict."""

    product_id: uuid.UUID
    product_name: str
    product_store_id: uuid.UUID
    store_name: str
    store_slug: str
    store_url: str
    package_size: str | None
    unit: str | None
    price_sek: float | None
    offer_price_sek: float
    unit_price_sek: float | None
    offer_type: str
    offer_details: str | None
    checked_at: datetime
    discount_percent: float
    best_alt_unit_price_sek: float | None
    best_alt_store: str | None
    best_alt_package_size: str | None
    verdict: str
    savings_per_unit_sek: float | None


def _rounded_unit_price(price: Decimal | None, quantity: Decimal | None) -> float | None:
    """kr/unit via domain.pricing (THE definition), rounded at this response boundary."""
    value = unit_price_py(price, quantity)
    if value is None:
        return None
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _effective(price_point: PricePoint) -> Decimal | None:
    """The price actually paid: the offer when there is one, else the regular price."""
    if price_point.offer_price_sek is not None:
        return price_point.offer_price_sek
    return price_point.price_sek


async def current_deals(session: AsyncSession, store_type: str | None = None) -> list[DealRow]:
    """Every link whose LATEST price point is an offer at most 7 days old, with verdicts.

    Ordering is RECENCY, deliberately kept from both former copies: ranking on the
    verdict or the margin is a VIEW decision (the portal groups client-side, the email
    groups per butik) — the wire contract stays stable underneath them.

    The best alternative is the cheapest CURRENT kr/unit among the product's OTHER links
    — latest point per link, no staleness cutoff, because it is the platform's best
    knowledge of what the shelf says elsewhere.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=DEALS_WINDOW_DAYS)

    latest = (
        select(
            PricePoint.product_store_id.label("ps_id"),
            func.max(PricePoint.checked_at).label("checked_at"),
        )
        .group_by(PricePoint.product_store_id)
        .subquery()
    )

    stmt = (
        select(PricePoint, ProductStore, Product, Store)
        .join(
            latest,
            (PricePoint.product_store_id == latest.c.ps_id)
            & (PricePoint.checked_at == latest.c.checked_at),
        )
        .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
        .join(Product, ProductStore.product_id == Product.id)
        .join(Store, ProductStore.store_id == Store.id)
        .where(ProductStore.is_active.is_(True))
        .where(PricePoint.offer_price_sek.is_not(None))
        .where(PricePoint.checked_at >= cutoff)
        .order_by(PricePoint.checked_at.desc())
    )
    if store_type:
        stmt = stmt.where(Store.store_type == store_type)

    rows = (await session.execute(stmt)).all()

    # Cheapest CURRENT kr/unit per product across ALL its links. This is what turns
    # "20 % rabatt" into a decision: the discount is the STORE's framing, the
    # cross-link unit price is ours.
    alternatives: dict[uuid.UUID, list[tuple[uuid.UUID, float, str, str | None]]] = {}
    if rows:
        product_ids = {product.id for _, _, product, _ in rows}
        alt_latest = (
            select(
                PricePoint.product_store_id.label("ps_id"),
                func.max(PricePoint.checked_at).label("checked_at"),
            )
            .group_by(PricePoint.product_store_id)
            .subquery()
        )
        alt_stmt = (
            select(ProductStore, Store, PricePoint)
            .join(Store, ProductStore.store_id == Store.id)
            .join(alt_latest, alt_latest.c.ps_id == ProductStore.id)
            .join(
                PricePoint,
                (PricePoint.product_store_id == alt_latest.c.ps_id)
                & (PricePoint.checked_at == alt_latest.c.checked_at),
            )
            .where(ProductStore.product_id.in_(product_ids))
        )
        for alt_ps, alt_store, alt_pp in (await session.execute(alt_stmt)).all():
            alt_unit_price = _rounded_unit_price(_effective(alt_pp), alt_ps.package_quantity)
            if alt_unit_price is None:
                continue
            alternatives.setdefault(alt_ps.product_id, []).append(
                (
                    alt_ps.id,
                    alt_unit_price,
                    link_store_name(alt_ps, alt_store),
                    alt_ps.package_size,
                )
            )

    deals: list[DealRow] = []
    for price_point, product_store, product, store in rows:
        discount_percent = 0.0
        if price_point.price_sek and price_point.offer_price_sek:
            discount_percent = (
                (float(price_point.price_sek) - float(price_point.offer_price_sek))
                / float(price_point.price_sek)
                * 100
            )

        alts = [a for a in alternatives.get(product.id, []) if a[0] != product_store.id]
        best_alt = min(alts, key=lambda a: a[1]) if alts else None
        best_alt_unit = best_alt[1] if best_alt else None
        unit_price = _rounded_unit_price(_effective(price_point), product_store.package_quantity)

        deals.append(
            DealRow(
                product_id=product.id,
                product_name=product.name,
                product_store_id=product_store.id,
                store_name=link_store_name(product_store, store),
                store_slug=store.slug,
                store_url=product_store.store_url,
                package_size=product_store.package_size,
                unit=product.unit,
                price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                offer_price_sek=float(price_point.offer_price_sek),
                unit_price_sek=unit_price,
                # Swedish fallback — this string reaches the UI badge and the email as-is.
                offer_type=price_point.offer_type or "erbjudande",
                offer_details=price_point.offer_details,
                checked_at=price_point.checked_at,
                discount_percent=discount_percent,
                best_alt_unit_price_sek=best_alt_unit,
                best_alt_store=best_alt[2] if best_alt else None,
                best_alt_package_size=best_alt[3] if best_alt else None,
                verdict=classify_deal(unit_price, best_alt_unit),
                savings_per_unit_sek=deal_savings(unit_price, best_alt_unit),
            )
        )

    return deals
