"""Tests for domain/deals.py — THE deal verdict and margin.

The query itself is exercised through its consumers (the /deals route in test_api.py and
service.get_current_deals in test_service.py, both over mocked sessions); these tests pin
the pure judgement, which the portal's classifyDeal/dealSavings now merely READ — plus a
real-Postgres check of the one WHERE clause a mocked session can never exercise.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.deals import (
    DEAL_BEST,
    DEAL_UNKNOWN,
    DEAL_WORSE,
    classify_deal,
    current_deals,
    deal_savings,
)
from domain.models import PricePoint, Product, ProductStore, Store
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
        store = (
            await db_session.execute(select(Store).where(Store.slug == "apotea"))
        ).scalar_one()
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
