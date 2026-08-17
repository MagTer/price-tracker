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
    session,
    product: Product,
    store: Store,
    quantity: str | None = "1",
    active: bool = True,
    package_size: str | None = None,
) -> ProductStore:
    link = ProductStore(
        product_id=product.id,
        store_id=store.id,
        store_url=f"https://example.se/{uuid.uuid4()}",
        package_quantity=Decimal(quantity) if quantity else None,
        package_size=package_size,
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
    raw_data: dict | None = None,
) -> PricePoint:
    return PricePoint(
        product_store_id=link.id,
        price_sek=Decimal(price),
        offer_price_sek=Decimal(offer) if offer else None,
        store_unit_price_sek=Decimal(printed) if printed else None,
        raw_data=raw_data,
        checked_at=_now() - timedelta(minutes=age_minutes),
    )


def test_expected_source_covers_every_structured_extractor() -> None:
    """EXPECTED_SOURCE must track the extractor registries, or the NEXT store added
    silently gets no tier-regression coverage — precisely the "checks green, campaigns
    dark" failure validation.py exists for. Clas Ohlson is exempt BY DESIGN: its
    extractor returns None when the page adds nothing beyond JSON-LD, so "jsonld" is
    a normal answer there and judging it would be a standing false alarm.
    """
    from domain.parser import _API_EXTRACTORS, _HTML_EXTRACTORS

    deliberately_absent = {"clasohlson"}
    structured = (set(_API_EXTRACTORS) | set(_HTML_EXTRACTORS)) - deliberately_absent
    assert structured == set(EXPECTED_SOURCE), (
        "A store has a structured extractor but no EXPECTED_SOURCE entry (or vice versa) — "
        "its ladder can degrade to JSON-LD with every check reporting ok and nothing "
        f"alarming. Registries: {sorted(structured)}, EXPECTED_SOURCE: "
        f"{sorted(EXPECTED_SOURCE)}. If the absence is deliberate (a Clas Ohlson-shaped "
        "extractor), add it to deliberately_absent WITH the reasoning."
    )


@pytest.mark.integration
class TestTierRegressions:
    @pytest.mark.asyncio
    async def test_latest_ok_from_a_lower_tier_is_a_regression(self, db_session) -> None:
        ica = await _store(db_session, "ica")
        link = await _link(
            db_session, await _product(db_session, "Jättefranska"), ica, package_size="500 g"
        )
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
        assert rows[0]["product_store_id"] == str(link.id)
        assert rows[0]["package_size"] == "500 g"

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
        # The id + package are what let the UI address THE link — the name pair is
        # ambiguous when one product has several links at the same butik.
        assert rows[0]["product_store_id"] == str(link.id)
        assert rows[0]["package_size"] is None

    @pytest.mark.asyncio
    async def test_printed_in_a_different_measure_is_not_judged(self, db_session) -> None:
        """The toalettpapper false alarm (prod, 2026-08-04): ICA prints the jämförpris
        in kr/KG while the product's unit is st (rulle) — printed 63.20, computed
        108.95 / 16 = 6.81, both CORRECT, ~89 % apart. A recognised measure that is not
        the product's unit means the comparison does not exist, not that it fails."""
        ica = await _store(db_session, "ica")
        product = await _product(db_session, "Toalettpapper", unit="st")
        link = await _link(db_session, product, ica, "16", package_size="16-p")
        db_session.add(
            _point(
                link,
                "108.95",
                "63.20",
                raw_data={"source": "ica_page", "unit_price_unit": "fop.price.per.kg"},
            )
        )
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_printed_in_the_same_measure_still_judges(self, db_session) -> None:
        """The measure gate must not blind the check where it CAN see: a kg-printed
        figure on a kg product that still contradicts the computed value is the real
        error class the validator exists for."""
        ica = await _store(db_session, "ica")
        product = await _product(db_session, "Kaffe", unit="kg")
        link = await _link(db_session, product, ica, "0.45", package_size="450 g")
        db_session.add(
            _point(
                link,
                "62.95",
                "80.00",  # store's own basis says 139.89 — the printed figure is off
                raw_data={"source": "ica_page", "unit_price_unit": "fop.price.per.kg"},
            )
        )
        await db_session.flush()

        rows = await unit_price_mismatches(db_session)

        assert len(rows) == 1
        assert rows[0]["package_size"] == "450 g"
        assert rows[0]["printed_unit"] == "kg"

    @pytest.mark.asyncio
    async def test_a_row_says_when_it_does_not_know_the_printed_measure(self, db_session) -> None:
        """`printed_unit` is None when nothing recorded one — and that must reach the UI.

        A reported row's measure is either equal to the product's unit or UNKNOWN (a
        positively different one is vetoed), and the two are not interchangeable on
        screen: Fel & luckor drew the printed figure with the PRODUCT's unit, so a Willys
        kr/kg jämförpris on a kit compared per styck was rendered "97,78 kr/st" — the
        wrong measure stated with confidence, in the row whose whole claim is that the
        two numbers disagree.
        """
        willys = await _store(db_session, "willys")
        product = await _product(db_session, "Middagskit", unit="st")
        link = await _link(db_session, product, willys, "1", package_size="1 st")
        db_session.add(
            _point(link, "26.40", "97.78", raw_data={"source": "willys_api"}),
        )
        await db_session.flush()

        rows = await unit_price_mismatches(db_session)

        assert len(rows) == 1
        assert rows[0]["unit"] == "st"
        assert rows[0]["printed_unit"] is None

    @pytest.mark.asyncio
    async def test_a_willys_point_that_records_its_measure_is_vetoed(self, db_session) -> None:
        """The other half of the same prod bug: the measure was AVAILABLE and unread.

        Same numbers as the row above — Lasagnette Dinnerkit, printed 97.78 kr/kg against
        26.40 kr/st computed. Willys publishes "kg" in `comparePriceUnit`, a field the
        extractor ignored while parsing the unit out of `comparePrice`, which never carries
        one. With the measure recorded the row is not a finding at all: two correct numbers
        in two different measures.
        """
        willys = await _store(db_session, "willys")
        product = await _product(db_session, "Middagskit", unit="st")
        link = await _link(db_session, product, willys, "1", package_size="1 st")
        db_session.add(
            _point(
                link,
                "26.40",
                "97.78",
                raw_data={"source": "willys_api", "comparison_unit": "/kg"},
            )
        )
        await db_session.flush()

        assert await unit_price_mismatches(db_session) == []

    @pytest.mark.asyncio
    async def test_unrecognised_measure_code_still_judges(self, db_session) -> None:
        """Only a POSITIVELY known, different measure may veto the comparison — an
        unknown code (Rusta's "/disk") must keep judging, or the check goes blind for
        every store whose extractor records no measure at all."""
        rusta = await _store(db_session, "rusta")
        product = await _product(db_session, "Maskindisk", unit="st")
        link = await _link(db_session, product, rusta, "100")
        db_session.add(
            _point(
                link,
                "99.00",
                "9.90",  # computed 0.99 — a real tenfold contradiction
                raw_data={"source": "rusta_page", "comparison_unit": "/disk"},
            )
        )
        await db_session.flush()

        rows = await unit_price_mismatches(db_session)

        assert len(rows) == 1

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
    async def test_gram_scale_amount_survives_storage_and_clears_the_mismatch(
        self, db_session
    ) -> None:
        """The Dippmix fix end-to-end: 24 g stored as 0.024 (Numeric(12, 4) since
        v0.45.0 — the old (10, 2) column rounded it back to 0.02 on write, so the
        mismatch could not be corrected at all) makes printed and computed agree."""
        store = await _store(db_session, "willys")
        link = await _link(db_session, await _product(db_session, "Dippmix"), store, "0.024")
        db_session.add(_point(link, "8.90", "370.83"))
        await db_session.flush()

        link_id = link.id
        db_session.expire(link)
        stored = (
            await db_session.execute(
                select(ProductStore.package_quantity).where(ProductStore.id == link_id)
            )
        ).scalar_one()
        assert stored == Decimal("0.024")
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
