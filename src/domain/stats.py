"""THE definition of the statistics view — one place, like pricing and schedule.

Everything here answers cross-product questions, which is why the rules below matter more
than the arithmetic:

**kr/unit is never summed or averaged across products.** Milk's kr/l plus cheese's kr/kg is
nonsense — the same invariant that keeps ``store_unit_price_sek`` unsortable (D-05). Anything
comparing whole products is therefore expressed as a PERCENTAGE change against that product's
own baseline, never as an absolute aggregate.

**A store comparison is only fair on a matched basket.** "ICA is 8 % more expensive" may be
computed only over products BOTH stores carry; over their full assortments it compares ICA's
selection against Willys' selection, not their prices.

**Carry forward, never interpolate.** A link checked on Monday and not again until the next
Monday has Monday's price all week — that is what the shelf said. The same rule the price
history modal's best-available series uses.

**Absence is not zero and not "no change".** A product observed once has no trend: its change
is None, not 0 %. Every panel also reports what it was computed from, so a thin answer reads
as thin rather than as a confident flat line.

READ-ONLY BY CONSTRUCTION. Nothing in this module or the endpoint over it may influence check
scheduling — not automatically and not via a button (decided 2026-07-28). Raising frequency
where prices move most means fetching hardest exactly where the WAF is, and a wall records no
price point at all, so the product that changes most often would come to look like it had
stopped changing. The feedback loop would eat the measurement it is built on.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import CheckAttempt, PricePoint, Product, ProductStore, Store, link_store_name
from domain.pricing import unit_price_py

_CENT = Decimal("0.01")


def _utc_now() -> datetime:
    """Naive UTC — the storage convention everywhere in this app."""
    return datetime.now(UTC).replace(tzinfo=None)


def _round(value: Decimal | None) -> float | None:
    """Money to the öre at the presentation boundary; None stays None (never 0.00)."""
    if value is None:
        return None
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _pct(new: Decimal, old: Decimal) -> float | None:
    """Percentage change against a baseline. None when the baseline is zero — a division we
    must not silently turn into infinity in a table a human reads as fact."""
    if old == 0:
        return None
    return float((new - old) / old * 100)


@dataclass
class _Link:
    """A link, with the display name every emission must use (link_store_name)."""

    link_id: Any
    product_id: Any
    store_name: str
    package_size: str | None
    quantity: Decimal | None
    last_checked_at: datetime | None


def _effective(point: PricePoint) -> Decimal | None:
    """The price actually paid — offer when there is one. The twin of service._effective_price."""
    if point.offer_price_sek is not None:
        return point.offer_price_sek
    return point.price_sek


def _cheapest(current: dict[Any, Decimal]) -> tuple[Any, Decimal] | None:
    """The winning (link, kr/unit) among the links whose price is currently known.

    Ties break on link id so two renders of identical data cannot disagree — the same
    stability rule the product table's best-price cell follows.
    """
    if not current:
        return None
    return min(current.items(), key=lambda item: (item[1], str(item[0])))


class _ProductSeries:
    """A product's cheapest-kr/unit over time, built by walking its links' points in order.

    The series is the product's ONE comparable number at each moment: the minimum kr/unit
    across every link whose price is known by then. It is a step function — a link keeps its
    last observed price until the next observation replaces it.
    """

    def __init__(self) -> None:
        self._current: dict[Any, Decimal] = {}
        self.baseline: Decimal | None = None  # value entering the period (carried forward)
        self.first: Decimal | None = None
        self.last: Decimal | None = None
        self.low: Decimal | None = None
        self.high: Decimal | None = None
        self.winner: Any = None
        self.stamps: set[datetime] = set()

    def seed(self, link_id: Any, unit_price: Decimal) -> None:
        """A price observed BEFORE the period — it is what the shelf said when the period
        opened, so it forms the baseline rather than being thrown away."""
        self._current[link_id] = unit_price

    def close_seed(self) -> None:
        """Freeze what the period opened at, and make it the current value too.

        A product whose last check predates the window is not gone — it is a price we still
        hold. Dropping it would empty the table the moment a narrow period is chosen, which
        looks like data loss rather than like "nothing happened in those weeks".
        """
        best = _cheapest(self._current)
        if best is None:
            return
        self.winner, self.baseline = best
        self.last = self.low = self.high = self.baseline

    def add(self, at: datetime, link_id: Any, unit_price: Decimal) -> None:
        self._current[link_id] = unit_price
        self.stamps.add(at)
        best = _cheapest(self._current)
        if best is None:
            return
        self.winner, value = best
        if self.first is None:
            self.first = value
        self.last = value
        self.low = value if self.low is None else min(self.low, value)
        self.high = value if self.high is None else max(self.high, value)

    def current_by_link(self) -> dict[Any, Decimal]:
        """Each link's latest known kr/unit — what every store is asking right now."""
        return dict(self._current)

    def best_excluding(self, link_id: Any) -> Decimal | None:
        """The cheapest kr/unit among the product's OTHER links, as of now in the walk.

        This is what turns a discount percentage into a decision: a 20 % campaign that is
        still pricier per unit than another store's ordinary price is not a saving.
        """
        others = {k: v for k, v in self._current.items() if k != link_id}
        best = _cheapest(others)
        return best[1] if best else None

    @property
    def start_value(self) -> Decimal | None:
        """What the product cost when the period opened: the carried-forward value if we had
        one, else its first observation inside the period."""
        return self.baseline if self.baseline is not None else self.first

    @property
    def change_pct(self) -> float | None:
        """None — not 0 — when there is nothing to compare against.

        A product observed on one day has no trend, and printing "0,0 %" for it would be a
        confident statement about a period we did not watch.
        """
        start, end = self.start_value, self.last
        if start is None or end is None:
            return None
        # Nothing observed inside the period: we are showing a carried-forward price, and
        # "unchanged" would be a claim about weeks we did not watch.
        if not self.stamps:
            return None
        # One observation and no earlier baseline: a single point is not a trend.
        if self.baseline is None and len(self.stamps) < 2:
            return None
        return _pct(end, start)


async def build_statistics(session: AsyncSession, *, weeks: int | None = None) -> dict[str, object]:
    """Everything the statistics page shows, computed in one pass over a tiny dataset.

    `weeks=None` means SINCE THE BEGINNING, which is the default: with a young history a
    fixed 12-week window renders four days of data inside a quarter-long frame and reads as
    broken. No caching, no materialized views — this is a few thousand rows for years to
    come, and a cache would be a second copy of the truth.
    """
    now = _utc_now()
    start = now - timedelta(weeks=weeks) if weeks else None

    link_rows = (
        await session.execute(
            select(ProductStore, Store, Product)
            .join(Store, ProductStore.store_id == Store.id)
            .join(Product, ProductStore.product_id == Product.id)
        )
    ).all()

    # Counted separately, NOT from the join: a product with no link yet is invisible
    # to the inner joins above, and using their count as the denominator makes the
    # coverage line claim "15 av 15" on a 16-product tracker — a health count that
    # cannot see the least healthy state.
    total_products = (await session.execute(select(func.count()).select_from(Product))).scalar_one()

    links: dict[Any, _Link] = {}
    products: dict[Any, Product] = {}
    links_by_product: dict[Any, list[_Link]] = defaultdict(list)
    stores_by_product: dict[Any, set[str]] = defaultdict(set)
    for product_store, store, product in link_rows:
        link = _Link(
            link_id=product_store.id,
            product_id=product.id,
            store_name=link_store_name(product_store, store),
            package_size=product_store.package_size,
            quantity=product_store.package_quantity,
            last_checked_at=product_store.last_checked_at,
        )
        links[link.link_id] = link
        products[product.id] = product
        links_by_product[product.id].append(link)
        stores_by_product[product.id].add(link.store_name)

    point_stmt = select(PricePoint).order_by(PricePoint.checked_at.asc())
    all_points = (await session.execute(point_stmt)).scalars().all()

    # Split on the period boundary: points before it seed the carry-forward baseline, points
    # inside it are the series. Done in Python because the whole table is small and two
    # queries with a window function would be a second definition of "the previous price".
    seed_points: dict[Any, PricePoint] = {}
    period_points: list[PricePoint] = []
    for point in all_points:
        if start is not None and point.checked_at < start:
            seed_points[point.product_store_id] = point  # ordered asc → last one wins
        else:
            period_points.append(point)

    series: dict[Any, _ProductSeries] = defaultdict(_ProductSeries)
    for link_id, point in seed_points.items():
        link = links.get(link_id)
        if link is None:
            continue
        value = unit_price_py(_effective(point), link.quantity)
        if value is not None:
            series[link.product_id].seed(link_id, value)
    for product_series in series.values():
        product_series.close_seed()

    # Offer quality, judged against what the product cost elsewhere AT THAT MOMENT — the one
    # number no store will ever print next to its own campaign.
    offers_by_store: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"offers": 0, "discount_sum": 0.0, "judged": 0, "was_cheapest": 0}
    )
    observations_by_store: dict[str, int] = defaultdict(int)

    for point in period_points:
        link = links.get(point.product_store_id)
        if link is None:
            continue
        observations_by_store[link.store_name] += 1
        product_series = series[link.product_id]
        value = unit_price_py(_effective(point), link.quantity)

        if point.offer_price_sek is not None:
            stats = offers_by_store[link.store_name]
            stats["offers"] += 1
            if point.price_sek:
                stats["discount_sum"] += float(
                    (point.price_sek - point.offer_price_sek) / point.price_sek * 100
                )
            # Judged BEFORE this point is folded in, so "elsewhere" means the alternatives as
            # they stood — including this link's own previous price is meaningless here.
            alternative = product_series.best_excluding(link.link_id)
            if value is not None and alternative is not None:
                stats["judged"] += 1
                if value <= alternative:
                    stats["was_cheapest"] += 1

        if value is not None:
            product_series.add(point.checked_at, link.link_id, value)

    product_rows = []
    for product_id, product in products.items():
        product_series = series.get(product_id)
        if product_series is None or product_series.last is None:
            continue
        winner = links.get(product_series.winner)
        product_rows.append(
            {
                "product_id": str(product_id),
                "name": product.name,
                "brand": product.brand,
                "category": product.category,
                "unit": product.unit,
                "current_unit_price": _round(product_series.last),
                "start_unit_price": _round(product_series.start_value),
                "change_pct": product_series.change_pct,
                "low_unit_price": _round(product_series.low),
                "high_unit_price": _round(product_series.high),
                "observations": len(product_series.stamps),
                "cheapest_store": winner.store_name if winner else None,
                "cheapest_package": winner.package_size if winner else None,
            }
        )
    product_rows.sort(key=lambda row: row["name"].lower())

    store_rows = _store_comparison(links_by_product, series, links)
    for row in store_rows:
        name = row["store_name"]
        offers = offers_by_store.get(name, {})
        row["observations"] = observations_by_store.get(name, 0)
        row["offers"] = offers.get("offers", 0)
        row["avg_discount_pct"] = (
            offers["discount_sum"] / offers["offers"] if offers.get("offers") else None
        )
        row["offers_judged"] = offers.get("judged", 0)
        row["offers_cheapest"] = offers.get("was_cheapest", 0)

    first_observation = all_points[0].checked_at if all_points else None
    coverage = _coverage(links, total_products, stores_by_product, series, now)
    # Step 0's table, reported as a bare fact: reliability panels need weeks of it, and until
    # then the honest thing to say is when collection started, not to imply an answer.
    attempt_stmt = select(func.count(CheckAttempt.id), func.min(CheckAttempt.checked_at))
    attempts = (await session.execute(attempt_stmt)).one()
    coverage["attempts"] = attempts[0] or 0
    coverage["attempts_since"] = attempts[1].isoformat() if attempts[1] else None

    return {
        "generated_at": now.isoformat(),
        "period": {
            "weeks": weeks,
            "start": start.isoformat() if start else None,
            "first_observation": first_observation.isoformat() if first_observation else None,
            "days_of_history": (now - first_observation).days if first_observation else 0,
            "observations": len(period_points),
        },
        "coverage": coverage,
        "products": product_rows,
        "stores": store_rows,
    }


def _store_comparison(
    links_by_product: dict[Any, list[_Link]],
    series: dict[Any, _ProductSeries],
    links: dict[Any, _Link],
) -> list[dict[str, Any]]:
    """Per store, on a MATCHED basket: how often it wins, and by how much it loses.

    Only products the store shares with at least one other store count. Over the full
    assortment the number would compare what each store happens to sell, which is a fact
    about shopping lists, not about prices.
    """
    wins: dict[str, int] = defaultdict(int)
    compared: dict[str, int] = defaultdict(int)
    premium_sum: dict[str, float] = defaultdict(float)
    premium_n: dict[str, int] = defaultdict(int)

    for product_id, product_links in links_by_product.items():
        product_series = series.get(product_id)
        if product_series is None or product_series.last is None:
            continue
        # Current kr/unit per STORE (a store may hold several links for one product — two
        # pack sizes — and its best one is what it offers the shopper).
        best_per_store: dict[str, Decimal] = {}
        current_prices = product_series.current_by_link()
        for link in product_links:
            value = current_prices.get(link.link_id)
            if value is None:
                continue
            best = best_per_store.get(link.store_name)
            if best is None or value < best:
                best_per_store[link.store_name] = value
        if len(best_per_store) < 2:
            continue  # nothing to match against — the product says nothing about any store

        for store_name, value in best_per_store.items():
            others = [v for name, v in best_per_store.items() if name != store_name]
            rival = min(others)
            compared[store_name] += 1
            if value <= rival:
                wins[store_name] += 1
            change = _pct(value, rival)
            if change is not None:
                premium_sum[store_name] += change
                premium_n[store_name] += 1

    rows = []
    for store_name in sorted(set(compared) | _store_names(links)):
        rows.append(
            {
                "store_name": store_name,
                "compared_products": compared.get(store_name, 0),
                "cheapest_count": wins.get(store_name, 0),
                "avg_premium_pct": (
                    premium_sum[store_name] / premium_n[store_name]
                    if premium_n.get(store_name)
                    else None
                ),
            }
        )
    return rows


def _store_names(links: dict[Any, _Link]) -> set[str]:
    """Every store display name that has a link — so a store with no comparable product still
    appears in the table, with zeros, rather than vanishing without explanation."""
    return {link.store_name for link in links.values()}


def _coverage(
    links: dict[Any, _Link],
    total_products: int,
    stores_by_product: dict[Any, set[str]],
    series: dict[Any, _ProductSeries],
    now: datetime,
) -> dict[str, Any]:
    """The health numbers — a to-do list, not statistics.

    Deliberately COUNTS only: the corresponding lists live on the Produkter page, which
    already has the facets to show them ("saknar mängd" is a filter there). A second list
    here would be a second place to maintain the same view.
    """
    checked = [link.last_checked_at for link in links.values() if link.last_checked_at]
    return {
        "products": total_products,
        "products_priced": sum(1 for s in series.values() if s.last is not None),
        "products_single_store": sum(1 for names in stores_by_product.values() if len(names) < 2),
        "links": len(links),
        "links_without_amount": sum(1 for link in links.values() if link.quantity is None),
        "links_never_checked": sum(1 for link in links.values() if link.last_checked_at is None),
        "oldest_check": min(checked).isoformat() if checked else None,
        "stalest_days": (now - min(checked)).days if checked else None,
    }


__all__ = ["build_statistics"]
