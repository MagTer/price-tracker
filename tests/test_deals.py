"""Tests for domain/deals.py — THE deal verdict and margin.

The query itself is exercised through its consumers (the /deals route in test_api.py and
service.get_current_deals in test_service.py, both over mocked sessions); these tests pin
the pure judgement, which the portal's classifyDeal/dealSavings now merely READ — plus a
real-Postgres check of the one WHERE clause a mocked session can never exercise.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.deals import (
    DEAL_BEST,
    DEAL_UNKNOWN,
    DEAL_WORSE,
    PRICE_LOW_WINDOW_DAYS,
    TIMING_GOOD,
    TIMING_POOR,
    TIMING_UNKNOWN,
    classify_deal,
    classify_timing,
    current_deals,
    deal_savings,
    seen_cheaper_pct,
)
from domain.models import CheckAttempt, PricePoint, Product, ProductStore, Store
from domain.tenant import DEFAULT_TENANT_ID


class TestClassifyDeal:
    def test_cheaper_than_alternative_is_best(self) -> None:
        assert classify_deal(5.00, 6.24) == DEAL_BEST

    def test_equal_price_is_best(self) -> None:
        """'Lika billigt' is still billigast — a tie must not read as a warning."""
        assert classify_deal(5.00, 5.00) == DEAL_BEST

    def test_pricier_than_alternative_is_worse(self) -> None:
        assert classify_deal(6.24, 5.00) == DEAL_WORSE

    def test_no_own_unit_price_is_unknown(self) -> None:
        """No amount on the link -> no honest comparison, never a guess."""
        assert classify_deal(None, 5.00) == DEAL_UNKNOWN

    def test_no_alternative_is_unknown(self) -> None:
        """The product's only link: nothing to compare against."""
        assert classify_deal(5.00, None) == DEAL_UNKNOWN


@pytest.mark.integration
class TestInactiveLinksAreNoAlternative:
    """Real Postgres: the best-alt query must carry the SAME is_active filter as the
    deal query. An inactive link's frozen last price is not a shelf anyone can buy
    from — letting it win as best_alt flips a genuine BEST to WORSE, which the weekly
    email then silently drops."""

    @staticmethod
    async def _link(
        session, product: Product, store: Store, quantity: str, active: bool = True
    ) -> ProductStore:
        link = ProductStore(
            product_id=product.id,
            store_id=store.id,
            store_url=f"https://www.apotea.se/{uuid.uuid4()}",
            package_quantity=Decimal(quantity),
            is_active=active,
        )
        session.add(link)
        await session.flush()
        return link

    @staticmethod
    def _point(link: ProductStore, price: str, offer: str | None = None) -> PricePoint:
        return PricePoint(
            product_store_id=link.id,
            price_sek=Decimal(price),
            offer_price_sek=Decimal(offer) if offer else None,
            offer_type="kampanj" if offer else None,
            checked_at=datetime.now(UTC).replace(tzinfo=None),
        )

    @pytest.mark.asyncio
    async def test_an_inactive_cheaper_link_does_not_flip_the_verdict(self, db_session) -> None:
        store = (await db_session.execute(select(Store).where(Store.slug == "apotea"))).scalar_one()
        product = Product(
            tenant_id=DEFAULT_TENANT_ID, name="Lambi", brand="Lambi", category=None, unit="st"
        )
        db_session.add(product)
        await db_session.flush()

        deal_link = await self._link(db_session, product, store, "24")
        dead_cheaper = await self._link(db_session, product, store, "24", active=False)
        live_pricier = await self._link(db_session, product, store, "8")

        db_session.add_all(
            [
                self._point(deal_link, "159.90", offer="120.00"),  # 5.00 kr/st on offer
                self._point(dead_cheaper, "96.00"),  # 4.00 kr/st — but nobody can buy it
                self._point(live_pricier, "48.00"),  # 6.00 kr/st — the real alternative
            ]
        )
        await db_session.flush()

        deals = await current_deals(db_session)

        assert len(deals) == 1
        deal = deals[0]
        assert deal.product_store_id == deal_link.id
        assert deal.verdict == DEAL_BEST  # the inactive 4.00 kr/st must not make it WORSE
        assert deal.best_alt_unit_price_sek == pytest.approx(6.00)
        assert deal.savings_per_unit_sek == pytest.approx(1.00)


class TestDealSavings:
    def test_positive_margin_when_the_offer_wins(self) -> None:
        assert deal_savings(5.00, 6.24) == pytest.approx(1.24)

    def test_negative_margin_when_the_offer_loses(self) -> None:
        assert deal_savings(6.24, 5.00) == pytest.approx(-1.24)

    def test_none_when_no_comparison_exists(self) -> None:
        """Never 0.0 — that would claim a tie we cannot see."""
        assert deal_savings(None, 5.00) is None
        assert deal_savings(5.00, None) is None


class TestClassifyTiming:
    """The SECOND judgement: is this a good moment, judged on the product's own floor.

    Born from prod, 2026-08-04: Bryggkaffe Mellanrost 450 g at ICA Björksätra, "2 för
    130 kr" = 144,44 kr/kg. Cheapest of the product's three links, so BEST — and 31 %
    above the 110,00 kr/kg the same coffee had cost at Willys nine days earlier. It led
    the buy list under the heading "Sex saker är billigast just nu".
    """

    def test_an_offer_that_sets_the_floor_is_a_good_moment(self) -> None:
        """What a genuine campaign looks like: five of the six deals in that prod
        snapshot sat exactly on their own floor, because the campaign IS the new low."""
        assert classify_timing(75.60, 75.60) == TIMING_GOOD

    def test_just_under_the_line_is_still_a_good_moment(self) -> None:
        assert classify_timing(109.00, 100.00) == TIMING_GOOD

    def test_at_the_line_is_a_poor_moment(self) -> None:
        assert classify_timing(110.00, 100.00) == TIMING_POOR

    def test_the_bryggkaffe_case(self) -> None:
        assert classify_timing(144.44, 110.00) == TIMING_POOR

    def test_no_floor_is_unknown(self) -> None:
        """No comparable history — never a guess in either direction."""
        assert classify_timing(144.44, None) == TIMING_UNKNOWN

    def test_no_own_unit_price_is_unknown(self) -> None:
        assert classify_timing(None, 110.00) == TIMING_UNKNOWN


class TestSeenCheaperPct:
    def test_zero_means_this_offer_is_the_floor(self) -> None:
        """0.0 is a real answer here, unlike in deal_savings: we HAVE watched this
        product and it has not been cheaper."""
        assert seen_cheaper_pct(75.60, 75.60) == pytest.approx(0.0)

    def test_the_bryggkaffe_margin(self) -> None:
        assert seen_cheaper_pct(144.44, 110.00) == pytest.approx(31.31, abs=0.01)

    def test_none_without_a_floor(self) -> None:
        assert seen_cheaper_pct(144.44, None) is None
        assert seen_cheaper_pct(None, 110.00) is None


@pytest.mark.integration
class TestTheFloorComesFromRealHistory:
    """Real Postgres: the floor query behind `timing`. A mocked session can assert the
    classification but never that the right rows reach it."""

    @staticmethod
    async def _link(
        session, product: Product, store: Store, quantity: str, active: bool = True
    ) -> ProductStore:
        link = ProductStore(
            product_id=product.id,
            store_id=store.id,
            store_url=f"https://www.apotea.se/{uuid.uuid4()}",
            package_quantity=Decimal(quantity),
            is_active=active,
        )
        session.add(link)
        await session.flush()
        return link

    @staticmethod
    def _point(
        link: ProductStore, price: str, offer: str | None = None, days_ago: int = 0
    ) -> PricePoint:
        return PricePoint(
            product_store_id=link.id,
            price_sek=Decimal(price),
            offer_price_sek=Decimal(offer) if offer else None,
            offer_type="kampanj" if offer else None,
            checked_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
        )

    async def _product(self, session, name: str) -> tuple[Product, Store]:
        store = (await session.execute(select(Store).where(Store.slug == "apotea"))).scalar_one()
        product = Product(
            tenant_id=DEFAULT_TENANT_ID, name=name, brand=None, category=None, unit="kg"
        )
        session.add(product)
        await session.flush()
        return product, store

    @pytest.mark.asyncio
    async def test_a_campaign_above_the_products_own_floor_is_a_poor_moment(
        self, db_session
    ) -> None:
        """The prod case end to end: cheapest link today, dear by its own history."""
        product, store = await self._product(db_session, "Bryggkaffe Mellanrost 450g")
        deal_link = await self._link(db_session, product, store, "0.45")
        other = await self._link(db_session, product, store, "0.45")

        db_session.add_all(
            [
                # The floor: 49,50 for 450 g = 110,00 kr/kg, nine days ago at the OTHER link.
                self._point(other, "67.90", offer="49.50", days_ago=9),
                self._point(other, "67.90", days_ago=1),  # 150,89 kr/kg — its price today
                # Today's campaign: 65,00 for 450 g = 144,44 kr/kg. Cheapest link, poor moment.
                self._point(deal_link, "69.46", offer="65.00"),
            ]
        )
        await db_session.flush()

        deals = await current_deals(db_session)

        assert len(deals) == 1
        deal = deals[0]
        assert deal.verdict == DEAL_BEST, "it IS the cheapest link — the verdict is unchanged"
        assert deal.timing == TIMING_POOR
        assert deal.lowest_unit_price_sek == pytest.approx(110.00)
        assert deal.seen_cheaper_pct == pytest.approx(31.31, abs=0.01)
        assert deal.lowest_store == "Apotea"

    @pytest.mark.asyncio
    async def test_a_campaign_that_sets_a_new_low_is_a_good_moment(self, db_session) -> None:
        product, store = await self._product(db_session, "Bregott Mellan 500g")
        link = await self._link(db_session, product, store, "0.5")
        db_session.add_all(
            [
                self._point(link, "48.90", days_ago=9),  # 97,80 kr/kg
                self._point(link, "48.90", offer="37.80"),  # 75,60 kr/kg — the new floor
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.timing == TIMING_GOOD
        assert deal.seen_cheaper_pct == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_a_price_older_than_the_window_is_not_the_floor(self, db_session) -> None:
        """A bargain from last spring is not evidence about this week's shelf — without
        the window one exceptional price would damn every campaign on that product
        forever."""
        product, store = await self._product(db_session, "Falukorv Klassikern 800g")
        link = await self._link(db_session, product, store, "0.8")
        db_session.add_all(
            [
                self._point(link, "39.90", offer="20.00", days_ago=PRICE_LOW_WINDOW_DAYS + 1),
                self._point(link, "39.90", days_ago=5),
                self._point(link, "39.90", offer="29.90"),
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.timing == TIMING_GOOD
        assert deal.lowest_unit_price_sek == pytest.approx(29.90 / 0.8, rel=1e-3)

    @pytest.mark.asyncio
    async def test_an_inactive_links_history_still_counts_as_a_floor(self, db_session) -> None:
        """The mirror of the best-alt rule, and deliberately the opposite answer: an
        inactive link is no ALTERNATIVE (nobody can buy from it) but it is real HISTORY
        — the price was charged, and "have I seen this cheaper" is a question about the
        past, not about a shelf we can reach today."""
        product, store = await self._product(db_session, "Toalettpapper Bad & Toalett")
        deal_link = await self._link(db_session, product, store, "1")
        dead = await self._link(db_session, product, store, "1", active=False)
        db_session.add_all(
            [
                self._point(dead, "10.00", days_ago=5),
                self._point(deal_link, "20.00", offer="15.00"),
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.verdict == DEAL_UNKNOWN, "the inactive link is no alternative"
        assert deal.timing == TIMING_POOR
        assert deal.lowest_unit_price_sek == pytest.approx(10.00)

    @pytest.mark.asyncio
    async def test_a_single_observation_is_no_floor(self, db_session) -> None:
        """A product whose only in-window observation is the deal itself must answer
        `unknown`, not `good`: "at its own floor — what a genuine campaign looks like"
        from one data point is the confident claim the span bar and stats.change_pct
        already refuse on the same row."""
        product, store = await self._product(db_session, "Ny produkt 500g")
        link = await self._link(db_session, product, store, "0.5")
        db_session.add(self._point(link, "48.90", offer="37.80"))
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.timing == TIMING_UNKNOWN
        assert deal.lowest_unit_price_sek is None
        assert deal.seen_cheaper_pct is None

    @pytest.mark.asyncio
    async def test_the_span_high_rides_the_same_window_as_the_floor(self, db_session) -> None:
        """The portal's span bar draws lowest..highest off the DEAL row — same 84-day
        walk as the floor, so the bar and `timing` can never disagree about history."""
        product, store = await self._product(db_session, "Kaffe 450g")
        link = await self._link(db_session, product, store, "0.45")
        db_session.add_all(
            [
                self._point(link, "79.90", days_ago=30),  # 177,56 kr/kg — the high
                self._point(link, "67.90", offer="49.50", days_ago=9),  # 110,00 — the floor
                self._point(link, "67.90", offer="65.00"),  # today: 144,44
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.lowest_unit_price_sek == pytest.approx(110.00)
        assert deal.highest_unit_price_sek == pytest.approx(177.56, abs=0.01)

    @pytest.mark.asyncio
    async def test_a_manual_floor_is_flagged(self, db_session) -> None:
        """A floor set by a hand-recorded price (a one-off förbokning) is marked so the
        weekly email can keep the demoted row — a price no shelf carries again must not
        silence twelve weeks of genuine campaigns."""
        product, store = await self._product(db_session, "Toapapper 32p")
        link = await self._link(db_session, product, store, "32")
        manual = self._point(link, "162.00", offer="109.00", days_ago=10)
        manual.raw_data = {"source": "manual"}
        db_session.add_all(
            [
                manual,  # 3,41 kr/st — the förbokning floor
                self._point(link, "162.00", offer="129.00"),  # today: 4,03 kr/st, 18 % above
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.timing == TIMING_POOR
        assert deal.floor_is_manual is True

    @pytest.mark.asyncio
    async def test_an_out_of_stock_link_is_no_alternative(self, db_session) -> None:
        """A latest point the store marked out of stock cannot veto a buyable deal:
        an empty shelf beats nothing. The deal row itself carries in_stock so
        consumers can MARK (never hide) a sold-out deal."""
        product, store = await self._product(db_session, "Tandkräm 75ml")
        deal_link = await self._link(db_session, product, store, "0.075")
        sold_out = await self._link(db_session, product, store, "0.075")

        cheap_but_gone = self._point(sold_out, "15.00", days_ago=1)  # 200 kr/l — cheapest
        cheap_but_gone.in_stock = False
        db_session.add_all(
            [
                cheap_but_gone,
                self._point(deal_link, "30.00", offer="22.50"),  # 300 kr/l on offer
            ]
        )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.in_stock is True
        assert deal.verdict == DEAL_UNKNOWN, "the sold-out link must not judge the deal WORSE"
        # The floor keeps counting the sold-out observation — the price WAS charged.
        assert deal.timing == TIMING_POOR
        assert deal.lowest_unit_price_sek == pytest.approx(200.00)

    @pytest.mark.asyncio
    async def test_a_broken_link_is_no_alternative(self, db_session) -> None:
        """A page whose last three non-blocked checks all failed (link_health's
        judgement) is a 404 holding a stale price — it must not veto a real deal.
        Broken links retry every morning and are never auto-retired, so without this
        a dead February URL judges August campaigns forever."""
        product, store = await self._product(db_session, "Diskmedel 500ml")
        deal_link = await self._link(db_session, product, store, "0.5")
        broken = await self._link(db_session, product, store, "0.5")

        db_session.add_all(
            [
                self._point(broken, "10.00", days_ago=90),  # frozen cheap price, out of window
                self._point(broken, "10.00", days_ago=40),  # ...and one inside the window
                self._point(deal_link, "30.00", offer="24.00"),
                self._point(deal_link, "30.00", days_ago=7),
            ]
        )
        for days_ago in (3, 2, 1):
            db_session.add(
                CheckAttempt(
                    store_id=store.id,
                    product_store_id=broken.id,
                    checked_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
                    outcome="fetch_failed",
                    source="scheduler",
                    detail="HTTP 404",
                )
            )
        await db_session.flush()

        deal = (await current_deals(db_session))[0]
        assert deal.product_store_id == deal_link.id
        assert deal.verdict == DEAL_UNKNOWN, "the dead link must not judge the deal"
        # ...while its recorded history still counts toward the floor.
        assert deal.timing == TIMING_POOR
        assert deal.lowest_unit_price_sek == pytest.approx(20.00)
