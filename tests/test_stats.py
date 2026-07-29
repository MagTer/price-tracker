"""domain.stats against a real database — the cross-product rules, not the arithmetic.

Every test here guards a rule that would fail SILENTLY and plausibly: a store comparison over
unmatched assortments still produces a number, a single observation still produces "0,0 %", and
a carried-forward price still produces a row. Wrong answers, all of them, and none of them look
wrong on the page.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.models import PricePoint, Product, ProductStore, Store
from domain.stats import build_statistics
from domain.tenant import DEFAULT_TENANT_ID

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _store(session, slug: str) -> Store:
    return (await session.execute(select(Store).where(Store.slug == slug))).scalar_one()


async def _product(session, name: str, unit: str = "liter") -> Product:
    product = Product(tenant_id=DEFAULT_TENANT_ID, name=name, brand=None, category=None, unit=unit)
    session.add(product)
    await session.flush()
    return product


async def _link(session, product: Product, store: Store, quantity: str | None) -> ProductStore:
    link = ProductStore(
        product_id=product.id,
        store_id=store.id,
        store_url=f"https://example.test/{uuid.uuid4()}",
        package_quantity=Decimal(quantity) if quantity is not None else None,
        last_checked_at=_now(),
    )
    session.add(link)
    await session.flush()
    return link


async def _point(
    session, link: ProductStore, price: str, *, days_ago: int = 0, offer: str | None = None
) -> None:
    session.add(
        PricePoint(
            product_store_id=link.id,
            price_sek=Decimal(price),
            offer_price_sek=Decimal(offer) if offer is not None else None,
            offer_type="kampanj" if offer is not None else None,
            checked_at=_now() - timedelta(days=days_ago),
        )
    )


def _row(payload: dict, name: str) -> dict:
    return next(row for row in payload["products"] if row["name"] == name)


def _store_row(payload: dict, name: str) -> dict:
    return next(row for row in payload["stores"] if row["store_name"] == name)


class TestProductTrend:
    @pytest.mark.asyncio
    async def test_cheapest_per_unit_wins_across_links_and_pack_sizes(self, db_session) -> None:
        """The product's one comparable number is the MINIMUM kr/unit, not any single link."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        milk = await _product(db_session, "Mellanmjölk")
        big = await _link(db_session, milk, ica, "1.5")  # 30 kr / 1.5 l = 20.00 kr/l
        small = await _link(db_session, milk, willys, "1")  # 17.90 kr/l — the winner
        await _point(db_session, big, "30.00")
        await _point(db_session, small, "17.90")
        await db_session.commit()

        payload = await build_statistics(db_session)
        row = _row(payload, "Mellanmjölk")

        assert row["current_unit_price"] == 17.90
        assert row["cheapest_store"] == "Willys"

    @pytest.mark.asyncio
    async def test_a_single_observation_has_no_trend(self, db_session) -> None:
        """0,0 % would be a confident claim about a period we did not watch."""
        willys = await _store(db_session, "willys")
        milk = await _product(db_session, "Mellanmjölk")
        link = await _link(db_session, milk, willys, "1")
        await _point(db_session, link, "17.90")
        await db_session.commit()

        row = _row(await build_statistics(db_session), "Mellanmjölk")

        assert row["change_pct"] is None
        assert row["observations"] == 1

    @pytest.mark.asyncio
    async def test_trend_is_measured_against_the_start_of_the_period(self, db_session) -> None:
        ica = await _store(db_session, "ica")
        milk = await _product(db_session, "Mellanmjölk")
        link = await _link(db_session, milk, ica, "1")
        await _point(db_session, link, "20.00", days_ago=20)
        await _point(db_session, link, "22.00", days_ago=1)
        await db_session.commit()

        row = _row(await build_statistics(db_session), "Mellanmjölk")

        assert row["change_pct"] == pytest.approx(10.0)
        assert row["low_unit_price"] == 20.00
        assert row["high_unit_price"] == 22.00

    @pytest.mark.asyncio
    async def test_a_price_from_before_the_window_is_carried_forward(self, db_session) -> None:
        """A link checked before the period still holds a price — that is what the shelf says.

        Dropping it would empty the table the moment a narrow period is chosen, which reads as
        data loss rather than as "nothing happened in those weeks".
        """
        ica = await _store(db_session, "ica")
        milk = await _product(db_session, "Mellanmjölk")
        link = await _link(db_session, milk, ica, "1")
        await _point(db_session, link, "20.00", days_ago=60)
        await db_session.commit()

        row = _row(await build_statistics(db_session, weeks=4), "Mellanmjölk")

        assert row["current_unit_price"] == 20.00
        # Nothing was observed INSIDE the window, so there is no change to report.
        assert row["change_pct"] is None
        assert row["observations"] == 0

    @pytest.mark.asyncio
    async def test_a_link_without_an_amount_cannot_enter_the_comparison(self, db_session) -> None:
        """D-02: no quantity, no kr/unit — and the coverage panel is where that surfaces."""
        ica = await _store(db_session, "ica")
        milk = await _product(db_session, "Mellanmjölk")
        await _link(db_session, milk, ica, None)
        await db_session.commit()

        payload = await build_statistics(db_session)

        assert payload["products"] == []
        assert payload["coverage"]["links_without_amount"] == 1
        assert payload["coverage"]["products_priced"] == 0


class TestStoreComparison:
    @pytest.mark.asyncio
    async def test_only_products_both_stores_carry_are_compared(self, db_session) -> None:
        """The matched-basket rule. Over full assortments the number compares what each store
        happens to SELL, which says nothing about its prices."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        milk = await _product(db_session, "Mellanmjölk")
        await _point(db_session, await _link(db_session, milk, ica, "1"), "22.00")
        await _point(db_session, await _link(db_session, milk, willys, "1"), "20.00")

        # Only ICA carries this one, and it is wildly expensive. It must not drag ICA's
        # average down — nobody can buy it cheaper anywhere we know of.
        caviar = await _product(db_session, "Kaviar", unit="kg")
        await _point(db_session, await _link(db_session, caviar, ica, "0.1"), "45.00")
        await db_session.commit()

        payload = await build_statistics(db_session)
        ica_row = _store_row(payload, "ICA")
        willys_row = _store_row(payload, "Willys")

        assert ica_row["compared_products"] == 1
        assert ica_row["cheapest_count"] == 0
        assert ica_row["avg_premium_pct"] == pytest.approx(10.0)
        assert willys_row["cheapest_count"] == 1
        assert willys_row["avg_premium_pct"] == pytest.approx(-9.0909, rel=1e-3)

    @pytest.mark.asyncio
    async def test_a_store_uses_its_own_best_pack_size(self, db_session) -> None:
        """Two links at one store are two pack sizes; the shopper gets the cheaper one."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        paper = await _product(db_session, "Hushållspapper", unit="st")
        await _point(db_session, await _link(db_session, paper, ica, "8"), "80.00")  # 10.00/st
        await _point(db_session, await _link(db_session, paper, ica, "24"), "180.00")  # 7.50/st
        await _point(db_session, await _link(db_session, paper, willys, "8"), "72.00")  # 9.00/st
        await db_session.commit()

        payload = await build_statistics(db_session)

        assert _store_row(payload, "ICA")["cheapest_count"] == 1
        assert _row(payload, "Hushållspapper")["current_unit_price"] == 7.50


class TestOfferQuality:
    @pytest.mark.asyncio
    async def test_a_campaign_that_is_not_actually_cheapest_is_counted_as_such(
        self, db_session
    ) -> None:
        """The number no store prints beside its own campaign: 30 % off a bad price."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        milk = await _product(db_session, "Mellanmjölk")
        willys_link = await _link(db_session, milk, willys, "1")
        ica_link = await _link(db_session, milk, ica, "1")
        # Willys' ordinary price is observed first, so it is the alternative in place.
        await _point(db_session, willys_link, "17.90", days_ago=2)
        await _point(db_session, ica_link, "30.00", offer="21.00", days_ago=1)
        await db_session.commit()

        row = _store_row(await build_statistics(db_session), "ICA")

        assert row["offers"] == 1
        assert row["offers_judged"] == 1
        assert row["offers_cheapest"] == 0
        assert row["avg_discount_pct"] == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_an_offer_with_nothing_to_compare_against_is_not_judged(self, db_session) -> None:
        """Unjudgeable is its own answer — counting it as a win would inflate every store."""
        ica = await _store(db_session, "ica")
        milk = await _product(db_session, "Mellanmjölk")
        await _point(db_session, await _link(db_session, milk, ica, "1"), "30.00", offer="21.00")
        await db_session.commit()

        row = _store_row(await build_statistics(db_session), "ICA")

        assert row["offers"] == 1
        assert row["offers_judged"] == 0
        assert row["offers_cheapest"] == 0


class TestCoverage:
    @pytest.mark.asyncio
    async def test_counts_what_blocks_a_comparison(self, db_session) -> None:
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        milk = await _product(db_session, "Mellanmjölk")
        await _point(db_session, await _link(db_session, milk, ica, "1"), "20.00")
        await _point(db_session, await _link(db_session, milk, willys, "1"), "19.00")
        lonely = await _product(db_session, "Kaviar", unit="kg")
        await _link(db_session, lonely, ica, None)
        await db_session.commit()

        coverage = (await build_statistics(db_session))["coverage"]

        assert coverage["products"] == 2
        assert coverage["products_priced"] == 1
        assert coverage["products_single_store"] == 1
        assert coverage["links_without_amount"] == 1

    @pytest.mark.asyncio
    async def test_a_product_with_no_link_counts_in_the_denominator(self, db_session) -> None:
        """The inner joins cannot see a linkless product, so counting them made the
        coverage line claim "1 av 1" on a two-product tracker — a health count blind
        to the least healthy state. "Saknar pris" on Produkter is the list; this is
        the honest denominator."""
        ica = await _store(db_session, "ica")
        linked = await _product(db_session, "Mellanmjölk")
        await _point(db_session, await _link(db_session, linked, ica, "1"), "20.00")
        await _product(db_session, "Ny produkt utan länk")
        await db_session.commit()

        coverage = (await build_statistics(db_session))["coverage"]

        assert coverage["products"] == 2
        assert coverage["products_priced"] == 1
