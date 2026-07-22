"""The schema, proven against a real Postgres (MODEL-02, MODEL-03, MODEL-08).

Every claim here is one the mocks-only suite structurally cannot make. A `MagicMock` grows any
attribute it is asked for, so it cannot notice a dropped column; a compiled SQL string cannot
notice that `NULLS LAST` was omitted; and no mock has an opinion about a UNIQUE constraint. The
phase whose deliverable IS the schema needs a database to say any of this out loud.

The five stores come from the migration's own seed rather than from inserts here — which also
proves the seed survived the in-place 0001 rewrite.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import PricePoint, Product, ProductStore, Store
from domain.service import PriceTrackerService
from domain.tenant import DEFAULT_TENANT_ID

pytestmark = pytest.mark.integration


def _now() -> datetime:
    """Naive UTC — the columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _store(session: AsyncSession, slug: str) -> Store:
    """A seeded store, straight from the migration."""
    store = (await session.execute(select(Store).where(Store.slug == slug))).scalar_one()
    return store


async def _lambi(session: AsyncSession) -> Product:
    """The canonical case: one abstract good, counted in pieces (rolls)."""
    product = Product(
        tenant_id=DEFAULT_TENANT_ID,
        name="Lambi Toalettpapper",
        brand="Lambi",
        category="hygiene",
        unit="st",
    )
    session.add(product)
    await session.flush()
    return product


def _link(
    product: Product, store: Store, url: str, quantity: Decimal | None, label: str | None
) -> ProductStore:
    return ProductStore(
        product_id=product.id,
        store_id=store.id,
        store_url=url,
        package_size=label,
        package_quantity=quantity,
    )


def _price(
    link: ProductStore, price: Decimal, *, offer: Decimal | None = None, minutes_ago: int = 0
) -> PricePoint:
    return PricePoint(
        product_store_id=link.id,
        price_sek=price,
        offer_price_sek=offer,
        in_stock=True,
        checked_at=_now() - timedelta(minutes=minutes_ago),
    )


# ---------------------------------------------------------------- MODEL-08: the migration


async def test_upgrade_head_creates_reshaped_schema(db_engine) -> None:
    """`upgrade head` on a FRESH database produces the reshaped schema — the whole phase in DDL."""

    def _reflect(sync_conn) -> dict[str, object]:
        inspector = inspect(sync_conn)
        return {
            "products": {c["name"] for c in inspector.get_columns("products")},
            "product_stores": {c["name"] for c in inspector.get_columns("product_stores")},
            "price_points": {c["name"] for c in inspector.get_columns("price_points")},
            "ps_unique": {uc["name"] for uc in inspector.get_unique_constraints("product_stores")},
        }

    async with db_engine.connect() as conn:
        schema = await conn.run_sync(_reflect)

    # The package moved to the link, and took the page's own reading with it (D-01/D-09).
    assert {"package_size", "package_quantity", "scraped_package_quantity"} <= schema[
        "product_stores"
    ]
    # 0002: the per-butik display label (ICA prices per physical butik). Its presence also
    # proves the 0001→0002 chain applied — the first real revision after the in-place-
    # rewritten 0001.
    assert "store_label" in schema["product_stores"]
    # ...and left nothing behind on the abstract good. Asserted by PREFIX, not by name: a
    # forgotten `package_anything` on products would reinstate the model this phase abolished.
    assert not [c for c in schema["products"] if c.startswith("package_")]

    # Unit price is computed on read (D-03/D-04). Only the store's PRINTED value is persisted.
    assert "store_unit_price_sek" in schema["price_points"]
    assert "unit_price_sek" not in schema["price_points"]

    # D-01: the URL is the link's natural key; the old pair-key constraint is gone — it forbade
    # the very thing this phase exists to enable.
    assert "uq_product_stores_store_url" in schema["ps_unique"]
    assert "uq_product_store" not in schema["ps_unique"]


async def test_migration_seeded_the_stores(db_session: AsyncSession) -> None:
    """The 0001 seed survived the in-place rewrite, and 0003 added Kronans."""
    slugs = (await db_session.execute(select(Store.slug).order_by(Store.slug))).scalars().all()
    assert list(slugs) == ["apotea", "doz", "ica", "kronans", "med24", "willys"]


def test_alembic_check_reports_no_drift(alembic_env) -> None:
    """The migration DDL and the ORM metadata agree — including by constraint NAME.

    Sync on purpose: alembic's env.py calls asyncio.run(), which cannot run inside a live loop.
    This project has already been bitten by exactly this drift once (a UniqueConstraint vs a
    unique index are different objects), so it is not hypothetical.
    """
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    with alembic_env() as cfg:
        try:
            command.check(cfg)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - only on drift
            pytest.fail(f"models.py and 0001_initial.py have drifted apart: {exc}")


# ---------------------------------------------------------------- MODEL-02: link identity


async def test_two_links_same_store_persist(db_session: AsyncSession) -> None:
    """Two pack sizes of one product at ONE store both persist (D-01).

    This is the phase's whole reason to exist. Under the old uq_product_store(product_id,
    store_id) it was an IntegrityError — the operator simply could not tell the app that Willys
    sells Lambi in both a 24-pack and an 8-pack.
    """
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)

    db_session.add(
        _link(lambi, willys, "https://www.willys.se/lambi-24p", Decimal("24"), "24-pack")
    )
    db_session.add(_link(lambi, willys, "https://www.willys.se/lambi-8p", Decimal("8"), "8-pack"))
    await db_session.commit()

    links = (
        (
            await db_session.execute(
                select(ProductStore).where(
                    ProductStore.product_id == lambi.id,
                    ProductStore.store_id == willys.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {link.package_quantity for link in links} == {Decimal("24.00"), Decimal("8.00")}
    assert len(links) == 2


async def test_duplicate_url_rejected(db_session: AsyncSession) -> None:
    """The same store PAGE cannot be tracked twice — store_url is globally unique (D-01).

    Named to match the `-k duplicate_url` selector VALIDATION.md pins for MODEL-02.
    """
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)
    url = "https://www.willys.se/lambi-24p"

    db_session.add(_link(lambi, willys, url, Decimal("24"), "24-pack"))
    await db_session.commit()

    db_session.add(_link(lambi, willys, url, Decimal("24"), "24-pack (igen)"))
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.flush()
    assert "uq_product_stores_store_url" in str(excinfo.value)
    await db_session.rollback()


async def test_duplicate_url_rejected_across_products(db_session: AsyncSession) -> None:
    """...and not even under a DIFFERENT product. One page describes one good."""
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)
    other = Product(tenant_id=DEFAULT_TENANT_ID, name="Något annat", unit="st")
    db_session.add(other)
    await db_session.flush()

    url = "https://www.willys.se/lambi-24p"
    db_session.add(_link(lambi, willys, url, Decimal("24"), "24-pack"))
    await db_session.commit()

    db_session.add(_link(other, willys, url, Decimal("24"), "24-pack"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ---------------------------------------------------------------- MODEL-03: the kr/unit ranking


async def test_unit_price_order_by_ranks_cheapest_per_unit(
    db_session: AsyncSession, session_factory
) -> None:
    """The ranking, executed by a real query planner — not compiled and eyeballed (AC#3).

    A 24-pack at 139.90 is 5.83/roll; an 8-pack at 59.90 is 7.49/roll; and a link with NO amount
    has no kr/unit at all. NULLS LAST is the load-bearing part: a link that still "needs amount"
    must sink to the bottom, not masquerade as the cheapest thing on the page. The naive
    comparator ranks it FIRST and looks entirely correct on inspection — which is exactly why
    this has to be run rather than read.
    """
    willys = await _store(db_session, "willys")
    ica = await _store(db_session, "ica")
    apotea = await _store(db_session, "apotea")
    lambi = await _lambi(db_session)

    cheap = _link(lambi, willys, "https://www.willys.se/lambi-24p", Decimal("24"), "24-pack")
    dear = _link(lambi, ica, "https://www.ica.se/lambi-8p", Decimal("8"), "8-pack")
    unknown = _link(lambi, apotea, "https://www.apotea.se/lambi", None, None)
    db_session.add_all([cheap, dear, unknown])
    await db_session.flush()

    db_session.add_all(
        [
            _price(cheap, Decimal("139.90")),
            _price(dear, Decimal("59.90")),
            # A price, but no amount to divide it by — and deliberately the LOWEST price on the
            # page. Any code that treats the missing quantity as a number (coalescing it to 1,
            # or to 0 without the NULLIF guard) ranks this link FIRST and recommends it. The
            # fixture only refutes that if the un-priced link is cheap enough to win when the
            # bug is present; at 99.00 it would sort last either way and the test would pass
            # vacuously.
            _price(unknown, Decimal("4.90")),
        ]
    )
    await db_session.commit()

    rows = await PriceTrackerService(session_factory).get_links_for_product(str(lambi.id))

    assert [row["store_slug"] for row in rows] == ["willys", "ica", "apotea"]
    assert [row["unit_price_sek"] for row in rows] == [5.83, 7.49, None]
    # The un-priced link says so out loud instead of rendering as a blank that reads as "free".
    assert rows[-1]["needs_amount"] is True
    assert rows[0]["needs_amount"] is False


async def test_offer_price_wins_the_computed_unit_price(
    db_session: AsyncSession, session_factory
) -> None:
    """kr/unit ranks on the price actually PAID: the offer when there is one.

    Without this, the 24-pack on campaign at 39.90 (1.66/roll) would be ranked on its 139.90
    shelf price and lose to an 8-pack it is four times cheaper than — the deal would be invisible
    on the one screen built to surface it.
    """
    willys = await _store(db_session, "willys")
    ica = await _store(db_session, "ica")
    lambi = await _lambi(db_session)

    on_offer = _link(lambi, willys, "https://www.willys.se/lambi-24p", Decimal("24"), "24-pack")
    regular = _link(lambi, ica, "https://www.ica.se/lambi-8p", Decimal("8"), "8-pack")
    db_session.add_all([on_offer, regular])
    await db_session.flush()

    db_session.add_all(
        [
            _price(on_offer, Decimal("139.90"), offer=Decimal("39.90")),
            _price(regular, Decimal("59.90")),
        ]
    )
    await db_session.commit()

    rows = await PriceTrackerService(session_factory).get_links_for_product(str(lambi.id))

    assert rows[0]["store_slug"] == "willys"
    assert rows[0]["unit_price_sek"] == pytest.approx(1.66)  # 39.90 / 24, not 139.90 / 24
    assert rows[1]["unit_price_sek"] == pytest.approx(7.49)


async def test_only_the_latest_price_point_ranks_a_link(
    db_session: AsyncSession, session_factory
) -> None:
    """A link's rank follows its LATEST observation, not its cheapest historical one."""
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)

    link = _link(lambi, willys, "https://www.willys.se/lambi-24p", Decimal("24"), "24-pack")
    db_session.add(link)
    await db_session.flush()
    db_session.add_all(
        [
            _price(link, Decimal("48.00"), minutes_ago=120),  # yesterday's bargain, 2.00/roll
            _price(link, Decimal("139.90"), minutes_ago=0),  # today's shelf price, 5.83/roll
        ]
    )
    await db_session.commit()

    rows = await PriceTrackerService(session_factory).get_links_for_product(str(lambi.id))
    assert len(rows) == 1
    assert rows[0]["price_sek"] == pytest.approx(139.90)
    assert rows[0]["unit_price_sek"] == pytest.approx(5.83)


async def test_a_link_with_no_price_point_still_appears(
    db_session: AsyncSession, session_factory
) -> None:
    """A link that has never been checked is still a link — and still says it needs an amount."""
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)
    db_session.add(_link(lambi, willys, "https://www.willys.se/lambi-24p", None, None))
    await db_session.commit()

    rows = await PriceTrackerService(session_factory).get_links_for_product(str(lambi.id))
    assert len(rows) == 1
    assert rows[0]["price_sek"] is None
    assert rows[0]["unit_price_sek"] is None
    assert rows[0]["needs_amount"] is True


async def test_scraped_quantity_conflict_is_flagged_not_adopted(
    db_session: AsyncSession, session_factory
) -> None:
    """The stored quantity is intent; the page's reading is evidence (D-07/D-09) — in real SQL.

    The flag is DERIVED from the two columns rather than persisted, so it self-clears the moment
    either number is corrected. Ranking still uses the operator's value, never the page's.
    """
    willys = await _store(db_session, "willys")
    lambi = await _lambi(db_session)

    link = _link(lambi, willys, "https://www.willys.se/lambi-24p", Decimal("24"), "24-pack")
    link.scraped_package_quantity = Decimal("12")  # the page disagrees
    db_session.add(link)
    await db_session.flush()
    db_session.add(_price(link, Decimal("139.90")))
    await db_session.commit()

    rows = await PriceTrackerService(session_factory).get_links_for_product(str(lambi.id))
    assert rows[0]["quantity_mismatch"] is True
    assert rows[0]["package_quantity"] == pytest.approx(24.0)  # NOT overwritten by the page
    assert rows[0]["unit_price_sek"] == pytest.approx(5.83)  # ranked on intent, not evidence


async def test_the_tests_never_touch_the_dev_database(db_engine) -> None:
    """The fixture CREATE-DROPs its database, so it had better be the throwaway one (T-04.1-24)."""
    assert db_engine.url.database == "price_tracker_test"
    assert uuid.UUID(str(DEFAULT_TENANT_ID))  # sanity: the tenant constant is a real UUID
