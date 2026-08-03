"""Tests for domain/validation.py — THE data-quality judgement.

Real Postgres, because both layers are latest-per-link queries (DISTINCT ON) that a
mocked session cannot exercise. The fixture values mirror the prod survey that shaped
the module (2026-08-03): Willys prints its jämförpris off the ORDINARIE, Clas Ohlson
off the CAMPAIGN price, and the one genuine error in prod (printed 370.83 vs computed
445.00) sat at 20 % deviation while quantity-rounding noise sat under 0.4 %.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.models import CheckAttempt, PricePoint, Product, ProductStore, Store
from domain.tenant import DEFAULT_TENANT_ID
from domain.validation import EXPECTED_SOURCE, tier_regressions, unit_price_mismatches


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _store(session, slug: str) -> Store:
    return (await session.execute(select(Store).where(Store.slug == slug))).scalar_one()


async def _product(session, name: str = "Testvara", unit: str = "kg") -> Product:
    product = Product(tenant_id=DEFAULT_TENANT_ID, name=name, brand=None, category=None, unit=unit)
    session.add(product)
    await session.flush()
    return product


async def _link(
    session, product: Product, store: Store, quantity: str | None = "1", active: bool = True
) -> ProductStore:
    link = ProductStore(
        product_id=product.id,
        store_id=store.id,
        store_url=f"https://example.se/{uuid.uuid4()}",
        package_quantity=Decimal(quantity) if quantity else None,
        is_active=active,
    )
    session.add(link)
    await session.flush()
    return link


def _attempt(
    link: ProductStore,
    outcome: str,
    source: str | None,
    age_minutes: int = 0,
) -> CheckAttempt:
    return CheckAttempt(
        store_id=link.store_id,
        product_store_id=link.id,
        checked_at=_now() - timedelta(minutes=age_minutes),
        outcome=outcome,
        source="scheduler",
        extraction_source=source,
    )


def _point(
    link: ProductStore,
    price: str,
    printed: str | None,
    offer: str | None = None,
    age_minutes: int = 0,
) -> PricePoint:
    return PricePoint(
        product_store_id=link.id,
        price_sek=Decimal(price),
        offer_price_sek=Decimal(offer) if offer else None,
        store_unit_price_sek=Decimal(printed) if printed else None,
        checked_at=_now() - timedelta(minutes=age_minutes),
    )


@pytest.mark.integration
class TestTierRegressions:
    @pytest.mark.asyncio
    async def test_latest_ok_from_a_lower_tier_is_a_regression(self, db_session) -> None:
        ica = await _store(db_session, "ica")
        link = await _link(db_session, await _product(db_session, "Jättefranska"), ica)
        db_session.add_all(
            [
                _attempt(link, "ok", "ica_page", age_minutes=60),
                _attempt(link, "ok", "jsonld", age_minutes=5),  # the shape changed
            ]
        )
        await db_session.flush()

        rows = await tier_regressions(db_session)

        assert len(rows) == 1
        assert rows[0]["product_name"] == "Jättefranska"
        assert rows[0]["expected_source"] == "ica_page"
        assert rows[0]["actual_source"] == "jsonld"

    @pytest.mark.asyncio
    async def test_healthy_latest_check_clears_older_regressions(self, db_session) -> None:
        ica = await _store(db_session, "ica")
        link = await _link(db_session, await _product(db_session), ica)
        db_session.add_all(
            [
                _attempt(link, "ok", "jsonld", age_minutes=60),
                _attempt(link, "ok", "ica_page", age_minutes=5),  # one good check clears
            ]
        )
        await db_session.flush()

        assert await tier_regressions(db_session) == []

    @pytest.mark.asyncio
    async def test_failed_and_blocked_attempts_do_not_judge(self, db_session) -> None:
        """A wall or a failure says nothing about which tier answers when the store does."""
        willys = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session), willys)
        db_session.add_all(
            [
                _attempt(link, "ok", "willys_api", age_minutes=60),
                _attempt(link, "blocked", None, age_minutes=10),
                _attempt(link, "fetch_failed", None, age_minutes=5),
            ]
        )
        await db_session.flush()

        assert await tier_regressions(db_session) == []

    @pytest.mark.asyncio
    async def test_clasohlson_jsonld_is_normal_not_a_regression(self, db_session) -> None:
        """The CO extractor returns None BY DESIGN when the page adds nothing beyond
        JSON-LD — judging it would make a healthy store a standing alarm."""
        clasohlson = await _store(db_session, "clasohlson")
        link = await _link(db_session, await _product(db_session), clasohlson)
        db_session.add(_attempt(link, "ok", "jsonld", age_minutes=5))
        await db_session.flush()

        assert "clasohlson" not in EXPECTED_SOURCE
        assert await tier_regressions(db_session) == []

    @pytest.mark.asyncio
    async def test_inactive_links_are_not_judged(self, db_session) -> None:
        ica = await _store(db_session, "ica")
        link = await _link(db_session, await _product(db_session), ica, active=False)
        db_session.add(_attempt(link, "ok", "jsonld", age_minutes=5))
        await db_session.flush()

        assert await tier_regressions(db_session) == []


@pytest.mark.integration
class TestUnitPriceMismatches:
    @pytest.mark.asyncio
    async def test_printed_matching_ordinarie_basis_is_consistent(self, db_session) -> None:
        # Live shape: jättefranska 27.30 / 1.1 kg, printed 24.82.
        store = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session), store, "1.1")
        db_session.add(_point(link, "27.30", "24.82"))
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_printed_matching_campaign_basis_is_consistent(self, db_session) -> None:
        # Clas Ohlson prints the jämförpris off the CAMPAIGN price (prod survey:
        # 149.46 / 94 = 1.59 while ordinarie says 1.91) — either basis must pass.
        store = await _store(db_session, "clasohlson")
        link = await _link(db_session, await _product(db_session, unit="st"), store, "94")
        db_session.add(_point(link, "179.90", "1.59", offer="149.46"))
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_contradiction_beyond_tolerance_is_flagged(self, db_session) -> None:
        # The real prod error the first survey found: printed 370.83, computed
        # 8.90 / 0.02 = 445.00 — the entered amount does not match the store's basis.
        store = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session, "Dippmix"), store, "0.02")
        db_session.add(_point(link, "8.90", "370.83"))
        await db_session.flush()

        rows = await unit_price_mismatches(db_session)

        assert len(rows) == 1
        assert rows[0]["product_name"] == "Dippmix"
        assert rows[0]["printed_unit_price_sek"] == pytest.approx(370.83)
        assert rows[0]["computed_unit_price_sek"] == pytest.approx(445.00)
        assert rows[0]["deviation_pct"] == pytest.approx(20.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_quantity_rounding_noise_stays_inside_tolerance(self, db_session) -> None:
        # 87.92 / 0.53 = 165.89 vs printed 165.26 (the store divided by 0.532): 0.38 %.
        store = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session), store, "0.53")
        db_session.add(_point(link, "87.92", "165.26"))
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_only_the_latest_point_is_judged(self, db_session) -> None:
        store = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session), store, "1")
        db_session.add_all(
            [
                _point(link, "10.00", "99.00", age_minutes=60),  # old contradiction
                _point(link, "10.00", "10.00", age_minutes=5),  # corrected since
            ]
        )
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_links_without_printed_figure_or_amount_are_not_judged(self, db_session) -> None:
        store = await _store(db_session, "willys")
        no_printed = await _link(db_session, await _product(db_session), store, "1")
        no_amount = await _link(db_session, await _product(db_session), store, None)
        db_session.add_all(
            [
                _point(no_printed, "10.00", None),
                _point(no_amount, "10.00", "99.00"),
            ]
        )
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []
