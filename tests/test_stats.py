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
from domain.stats import build_statistics, product_offer_occasions
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
    async def test_an_inactive_link_is_not_the_cheapest_store(self, db_session) -> None:
        """Same rule as deals.current_deals: an inactive link's frozen last price is
        not a shelf anyone can buy from. Without the filter Prisutveckling could name
        a deactivated butik as "billigast" while Att köpa refuses to — two pages
        disagreeing about the same product."""
        milk = await _product(db_session, "Mellanmjölk")
        ica = await _store(db_session, "ica")
        willys = await _store(db_session, "willys")
        dead = await _link(db_session, milk, ica, "1")
        dead.is_active = False
        live = await _link(db_session, milk, willys, "1")
        await _point(db_session, dead, "15.00")  # cheapest — but retired
        await _point(db_session, live, "20.00")
        await db_session.flush()

        payload = await build_statistics(db_session)
        row = _row(payload, "Mellanmjölk")
        assert row["cheapest_store"] == "Willys"
        assert row["current_unit_price"] == pytest.approx(20.00)

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


class TestStatsWire:
    """The HTTP layer over build_statistics — untested until v0.51.0. The frontend's
    whole Fel & luckor page and the sidebar severity badge read `data_quality` off this
    payload, and that key is composed IN THE ROUTE (deliberately: validation ignores the
    period filter), so no domain test can pin it."""

    @pytest.fixture
    def client(self, session_factory):
        import httpx

        from api.admin import get_db as admin_get_db
        from api.app import create_app
        from api.auth import Principal, get_principal

        app = create_app()

        async def override_auth() -> Principal:
            return Principal(email="reader@example.com", is_admin=False)

        async def override_get_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_principal] = override_auth
        app.dependency_overrides[admin_get_db] = override_get_db
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_payload_carries_data_quality_and_is_reader_readable(
        self, client, db_session
    ) -> None:
        async with client as c:
            r = await c.get("/stats")

        assert r.status_code == 200
        payload = r.json()
        # The keys every consumer reads — renderStatistics, renderLuckor, the badges.
        assert set(payload) >= {"period", "products", "stores", "coverage", "data_quality"}
        assert set(payload["data_quality"]) == {"tier_regressions", "unit_price_mismatches"}

    @pytest.mark.asyncio
    async def test_weeks_zero_means_since_start_and_positive_narrows(
        self, client, db_session
    ) -> None:
        """The route translates weeks=0 -> None (since start); a positive value passes
        through. Pinned via the period echo build_statistics returns."""
        async with client as c:
            all_time = (await c.get("/stats?weeks=0")).json()
            narrowed = (await c.get("/stats?weeks=4")).json()

        assert all_time["period"]["weeks"] is None or all_time["period"]["weeks"] == 0
        assert narrowed["period"]["weeks"] == 4


class TestOfferOccasions:
    """Pristillfällen — the only judgement in this app about the PAST.

    Everything else asks "is this the cheapest right now" (deals.verdict) or "is this cheap
    for this product" (deals.timing). This asks "was the campaign we showed you actually the
    best buy that week", which is the only feedback the tracker can give on its own advice.
    """

    async def test_a_campaign_is_judged_against_the_shelf_as_it_stood(self, db_session) -> None:
        """The alternative is each OTHER link's price carried forward from its last
        observation — not its price today, and not this link's own earlier price."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        coffee = await _product(db_session, "Bryggkaffe", unit="kg")
        ica_link = await _link(db_session, coffee, ica, "0.45")
        willys_link = await _link(db_session, coffee, willys, "0.5")

        # Willys observed once, three weeks ago, and not since: 60,00 / 0,5 = 120,00 kr/kg.
        await _point(db_session, willys_link, "60.00", days_ago=21)
        # ICA runs a campaign a week later at 62,50 / 0,45 = 138,89 kr/kg — cheaper than its
        # own ordinary price, and still dearer than the Willys price standing that week.
        await _point(db_session, ica_link, "79.90", days_ago=14, offer="62.50")
        await db_session.commit()

        rows = await product_offer_occasions(db_session, coffee.id)

        assert len(rows) == 1
        row = rows[0]
        assert row.was_cheapest is False
        assert row.unit_price_sek == pytest.approx(138.89)
        assert row.alternative_unit_price_sek == pytest.approx(120.0)
        assert row.alternative_store == "Willys"
        # And WHEN that alternative was last seen, so the row cannot imply we looked the
        # same morning: with weekly checks it can be six days old.
        assert row.alternative_seen_at is not None

    async def test_an_offer_with_nothing_to_compare_against_is_unjudged_not_lost(
        self, db_session
    ) -> None:
        """None, never False: calling an unjudgeable campaign "not cheapest" invents a loss
        out of a gap. It still appears — a row dropped here would not match the count."""
        willys = await _store(db_session, "willys")
        coffee = await _product(db_session, "Bryggkaffe", unit="kg")
        only = await _link(db_session, coffee, willys, "0.5")
        await _point(db_session, only, "79.90", days_ago=7, offer="59.90")
        await db_session.commit()

        rows = await product_offer_occasions(db_session, coffee.id)

        assert len(rows) == 1
        assert rows[0].was_cheapest is None
        assert rows[0].alternative_unit_price_sek is None

    async def test_a_retired_links_campaigns_stay_in_the_history(self, db_session) -> None:
        """Decided 2026-08-13: the rest of stats.py filters inactive links because
        "billigast" must not name a shelf you cannot buy from — but this table is history,
        and a campaign that really ran really ran. Flagged, never hidden."""
        ica = await _store(db_session, "ica")
        coffee = await _product(db_session, "Bryggkaffe", unit="kg")
        retired = await _link(db_session, coffee, ica, "0.45")
        retired.is_active = False
        await _point(db_session, retired, "79.90", days_ago=10, offer="62.50")
        await db_session.commit()

        rows = await product_offer_occasions(db_session, coffee.id)

        assert len(rows) == 1
        assert rows[0].link_is_active is False

    async def test_campaigns_before_the_period_seed_the_comparison_but_are_not_rows(
        self, db_session
    ) -> None:
        """A campaign that ran before the window is not in the window — but the price it
        left behind is what the next campaign is judged against."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        coffee = await _product(db_session, "Bryggkaffe", unit="kg")
        ica_link = await _link(db_session, coffee, ica, "0.45")
        willys_link = await _link(db_session, coffee, willys, "0.5")

        # Ten weeks back: outside a 4-week window. 50,00 / 0,5 = 100,00 kr/kg.
        await _point(db_session, willys_link, "60.00", days_ago=70, offer="50.00")
        await _point(db_session, ica_link, "79.90", days_ago=7, offer="62.50")
        await db_session.commit()

        rows = await product_offer_occasions(db_session, coffee.id, days=28)

        assert len(rows) == 1
        assert rows[0].store_name.startswith("ICA")
        # Judged against the carried 100,00 kr/kg, which is what Willys' shelf still said.
        assert rows[0].alternative_unit_price_sek == pytest.approx(100.0)
        assert rows[0].was_cheapest is False

    async def test_the_rows_and_the_per_store_counter_agree(self, db_session) -> None:
        """The Gotcha-4 guard: both resolve through was_cheapest(), so the number on
        Prisutveckling and the rows behind it cannot disagree about the same campaign."""
        ica, willys = await _store(db_session, "ica"), await _store(db_session, "willys")
        coffee = await _product(db_session, "Bryggkaffe", unit="kg")
        ica_link = await _link(db_session, coffee, ica, "0.45")
        willys_link = await _link(db_session, coffee, willys, "0.5")
        await _point(db_session, willys_link, "60.00", days_ago=21)
        await _point(db_session, ica_link, "79.90", days_ago=14, offer="62.50")
        await _point(db_session, ica_link, "79.90", days_ago=7, offer="40.00")
        await db_session.commit()

        rows = await product_offer_occasions(db_session, coffee.id)
        payload = await build_statistics(db_session)
        ica_row = _store_row(payload, "ICA")

        judged = [row for row in rows if row.was_cheapest is not None]
        assert len(judged) == ica_row["offers_judged"]
        assert sum(1 for row in judged if row.was_cheapest) == ica_row["offers_cheapest"]
