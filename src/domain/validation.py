"""THE data-quality judgement — does the tracker still SEE what the stores publish?

Born from a silent miss (2026-08-03): ICA's JSON-LD never carries campaigns, so every
ICA deal was invisible while every check reported "ok" — no error anywhere, plausible
prices, wrong answer. The store-HTML extractor fixed that instance; this module alarms
on the CLASS, using redundancy the tracker already records:

1. **Tier regression** — ``check_attempts.extraction_source`` names which ladder tier
   answered. For a store whose best tier is a structured one (ICA/Rusta/Lyko page state,
   Willys API), a link whose LATEST successful check came from a lower tier means the
   structured source changed shape and the ladder silently degraded to JSON-LD/LLM —
   checks keep passing, campaigns and jämförpris go dark. Clas Ohlson is deliberately
   NOT judged: its extractor returns None BY DESIGN when the page adds nothing beyond
   JSON-LD (no rea, no jfr-pris), so "jsonld" is a normal source there, not a regression.

2. **Jämförpris consistency** — the store's PRINTED unit price against our computed
   ``price / package_quantity``. When they disagree beyond rounding, either the price or
   the package amount is extracted/entered wrong: the check that catches both failure
   classes with data already stored (it would have lit up on the v0.41.1 Willys 31.10
   and found a real prod error — Fresh Island Dippmix, printed 370.83 vs computed
   445.00 — on its first survey). The printed figure follows the ORDINARIE at Willys and
   ICA but the CAMPAIGN price at Clas Ohlson (both verified on prod data 2026-08-03),
   so a link is consistent when EITHER basis matches; refusing to pick one is what keeps
   Clas Ohlson from becoming a standing false alarm.

Read-only by construction, like statistics: the validator observes, a human acts.
Nothing here may write, and nothing here may influence check scheduling.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import CheckAttempt, PricePoint, Product, ProductStore, Store, link_store_name
from domain.pricing import printed_measure

# The best tier per store slug — what extraction_source SHOULD say on a healthy check.
# Only stores whose extractor answers whenever the page is healthy belong here (see the
# module docstring on why Clas Ohlson does not).
EXPECTED_SOURCE: dict[str, str] = {
    "ica": "ica_page",
    "rusta": "rusta_page",
    "lyko": "lyko_page",
    "jysk": "jysk_page",
    "willys": "willys_api",
}

# A mismatch must clear BOTH bars: printed jämförpris values are öre-quantized off a
# possibly rounded package amount (0.532 kg stored as 0.53 gives 0.4 %), and on
# kr-per-styck values an öre of rounding is a large relative slice.
RELATIVE_TOLERANCE = Decimal("0.015")
ABSOLUTE_TOLERANCE_SEK = Decimal("0.10")

# The printed jämförpris and the computed kr/unit are only comparable when they are in the
# SAME measure — the exact trap models.py documents on store_unit_price_sek ("kr/rulle vs
# kr/pack vs kr/100g"). ICA prints toalettpapper in kr/KG while the product's unit is st
# (rulle): printed 63.20 vs computed 6.81, both correct, ~89 % "deviation" — the false
# alarm the first Fel & luckor survey was full of. Reading that measure off raw_data lives
# in pricing.printed_measure, which the links panel also uses to LABEL the printed figure;
# a second copy here would let the row's label and this veto disagree about one number.


async def data_quality(session: AsyncSession) -> dict[str, Any]:
    """Both validator layers in one read — the shape the /stats payload carries."""
    return {
        "tier_regressions": await tier_regressions(session),
        "unit_price_mismatches": await unit_price_mismatches(session),
    }


async def tier_regressions(session: AsyncSession) -> list[dict[str, Any]]:
    """Active links whose LATEST successful check did not come from the store's best tier.

    Judged on the latest ``outcome == "ok"`` attempt only: failed and blocked attempts
    say nothing about which tier answers when the store does (link_health owns failures,
    the breaker owns walls). One healthy check clears a link by construction.
    """
    latest_ok = (
        select(
            CheckAttempt.product_store_id,
            CheckAttempt.extraction_source,
            CheckAttempt.checked_at,
        )
        .where(CheckAttempt.outcome == "ok", CheckAttempt.product_store_id.isnot(None))
        .distinct(CheckAttempt.product_store_id)
        .order_by(CheckAttempt.product_store_id, CheckAttempt.checked_at.desc())
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ProductStore,
                Store,
                Product.name,
                latest_ok.c.extraction_source,
                latest_ok.c.checked_at,
            )
            .join(ProductStore, ProductStore.id == latest_ok.c.product_store_id)
            .join(Store, Store.id == ProductStore.store_id)
            .join(Product, Product.id == ProductStore.product_id)
            .where(ProductStore.is_active.is_(True), Store.slug.in_(EXPECTED_SOURCE))
        )
    ).all()

    regressions = []
    for link, store, product_name, source, checked_at in rows:
        expected = EXPECTED_SOURCE[store.slug]
        if source == expected:
            continue
        regressions.append(
            {
                # The id and the package are what make a row ACTIONABLE: one product can
                # have several links at the same butik (different pack sizes), so the
                # name pair alone cannot address the link a fix should land on.
                "product_store_id": str(link.id),
                "product_name": product_name,
                "store_name": link_store_name(link, store),
                "package_size": link.package_size,
                "expected_source": expected,
                "actual_source": source or "unknown",
                "checked_at": checked_at.isoformat(),
            }
        )
    regressions.sort(key=lambda row: (row["store_name"], row["product_name"]))
    return regressions


async def unit_price_mismatches(session: AsyncSession) -> list[dict[str, Any]]:
    """Active links whose printed jämförpris contradicts price / package_quantity.

    Only links where the comparison is possible are judged (a printed figure AND an
    amount on the link, and — when the extractor recorded which measure the store
    prints in — the SAME measure as the product's unit); a link passes when the
    printed value matches EITHER the ordinarie basis or the effective (campaign)
    basis within tolerance — which store prints which basis is a store fact we
    deliberately do not model (see module docstring). ``checked`` rides along so the
    UI can say what the count is out of.
    """
    latest = (
        select(
            PricePoint.product_store_id,
            PricePoint.price_sek,
            PricePoint.offer_price_sek,
            PricePoint.store_unit_price_sek,
            PricePoint.raw_data,
        )
        .distinct(PricePoint.product_store_id)
        .order_by(PricePoint.product_store_id, PricePoint.checked_at.desc())
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ProductStore,
                Store,
                Product.name,
                Product.unit,
                latest.c.price_sek,
                latest.c.offer_price_sek,
                latest.c.store_unit_price_sek,
                latest.c.raw_data,
            )
            .join(ProductStore, ProductStore.id == latest.c.product_store_id)
            .join(Store, Store.id == ProductStore.store_id)
            .join(Product, Product.id == ProductStore.product_id)
            .where(
                ProductStore.is_active.is_(True),
                latest.c.store_unit_price_sek.isnot(None),
                ProductStore.package_quantity.isnot(None),
            )
        )
    ).all()

    mismatches = []
    for link, store, product_name, unit, price, offer, printed, raw_data in rows:
        quantity = link.package_quantity
        if not quantity or quantity <= 0 or not printed or printed <= 0:
            continue
        # A printed figure in a DIFFERENT measure than the product's unit contradicts
        # nothing — kr/kg against kr/rulle is two true statements about one shelf.
        measure = printed_measure(raw_data)
        if measure is not None and measure != unit:
            continue
        candidates = [price / quantity]
        if offer is not None:
            candidates.append(offer / quantity)
        best = min(candidates, key=lambda computed: abs(computed - printed))
        deviation = abs(best - printed)
        if deviation <= ABSOLUTE_TOLERANCE_SEK or deviation / printed <= RELATIVE_TOLERANCE:
            continue
        mismatches.append(
            {
                # Same rule as the regression rows: names alone cannot address one of
                # several same-butik links, so the id and package ride along.
                "product_store_id": str(link.id),
                "product_name": product_name,
                "store_name": link_store_name(link, store),
                "package_size": link.package_size,
                "unit": unit,
                "printed_unit_price_sek": float(printed.quantize(Decimal("0.01"))),
                "computed_unit_price_sek": float(best.quantize(Decimal("0.01"))),
                "deviation_pct": float((deviation / printed * 100).quantize(Decimal("0.1"))),
            }
        )
    mismatches.sort(key=lambda row: -row["deviation_pct"])
    return mismatches


__all__ = ["EXPECTED_SOURCE", "data_quality", "tier_regressions", "unit_price_mismatches"]
