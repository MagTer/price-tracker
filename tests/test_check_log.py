"""check_attempts — the durable record of what every outgoing check produced.

The table exists because price_points only records SUCCESS. A failure writes nothing, and a
block does not even set last_checked_at (v0.29.2), so "how often does ICA answer us" has never
been answerable from stored data — only from a log buffer that resets on restart. These tests
guard the two properties that make the record trustworthy:

  1. EVERY outcome is recorded, from ONE place, so no caller can quietly opt out.
  2. Recording can never break a check, and never disappears with the caller's transaction.

Property 2's second half is the subtle one and needs a real database: the interactive endpoints
raise before they commit, so a row written into the request's session would be rolled back —
losing exactly the failures worth having.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from domain.models import CheckAttempt, PricePoint, Product, ProductStore, Store
from domain.result import PriceExtractionResult, extraction_source
from domain.service import perform_price_check
from domain.tenant import DEFAULT_TENANT_ID
from infra.check_log import CheckAttemptLog


class _RecordingLog:
    """A stub ICheckAttemptLog that keeps what it was told."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _extraction(price: str | None, source: str | None = None) -> PriceExtractionResult:
    raw: dict = {}
    if source is not None:
        raw["source"] = source
    return PriceExtractionResult(
        price_sek=Decimal(price) if price is not None else None,
        store_unit_price_sek=None,
        offer_price_sek=None,
        offer_type=None,
        offer_details=None,
        in_stock=True,
        confidence=0.9,
        pack_size=None,
        package_amount=None,
        package_unit=None,
        raw_response=raw,
    )


def _fixtures():
    """(product_store, product, store, session, fetcher, parser) for one check."""
    product_store = MagicMock()
    product_store.id = uuid.uuid4()
    product_store.store_url = "https://www.apotea.se/produkt"
    product_store.package_quantity = Decimal("10")
    product_store.scraped_package_quantity = None
    product_store.last_checked_at = None

    product = MagicMock()
    product.name = "Lambi"
    product.unit = "st"

    store = MagicMock()
    store.id = uuid.uuid4()
    store.name = "Apotea"
    store.slug = "apotea"

    session = AsyncMock()
    prior = MagicMock()
    prior.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=prior)

    fetcher = AsyncMock()
    fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "text", "html": "<html>"})

    parser = MagicMock()
    parser.extract_price = AsyncMock()
    parser.enrich_with_llm = AsyncMock(side_effect=lambda base, **kwargs: base)

    return product_store, product, store, session, fetcher, parser


class TestExtractionSource:
    """THE reader of raw_response["source"] — one definition for three former copies."""

    @pytest.mark.parametrize(
        "source",
        ["willys_api", "rusta_page", "clasohlson_page", "jsonld", "llm:deepseek"],
    )
    def test_reports_the_stamped_tier(self, source: str) -> None:
        assert extraction_source(_extraction("10.00", source)) == source

    def test_missing_source_is_unknown_not_llm(self) -> None:
        """Absence must not read as "the LLM answered" — that was the old else-branch's flaw."""
        assert extraction_source(_extraction("10.00")) == "unknown"

    def test_no_extraction_at_all_is_unknown(self) -> None:
        assert extraction_source(None) == "unknown"

    def test_non_dict_raw_response_is_unknown(self) -> None:
        result = _extraction("10.00")
        result.raw_response = "not a dict"  # type: ignore[assignment]
        assert extraction_source(result) == "unknown"


class TestPerformPriceCheckRecordsEveryOutcome:
    @pytest.mark.asyncio
    async def test_success_is_recorded_with_its_tier(self) -> None:
        ps, product, store, session, fetcher, parser = _fixtures()
        parser.extract_price.return_value = _extraction("139.90", "jsonld")
        log = _RecordingLog()

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=log,
            attempt_source="scheduler",
        )

        assert len(log.calls) == 1
        call = log.calls[0]
        assert call["outcome"] == "ok"
        assert call["source"] == "scheduler"
        assert call["extraction_source"] == "jsonld"
        assert call["store_id"] == store.id
        assert call["product_store_id"] == ps.id
        assert call["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_bot_wall_is_recorded_as_blocked_not_as_a_fetch_failure(self) -> None:
        """The distinction the whole table is for: a wall is the STORE refusing, not a dead page."""
        ps, product, store, session, fetcher, parser = _fixtures()
        fetcher.fetch = AsyncMock(
            return_value={"ok": False, "error": "blocked (HTTP 202)", "blocked": True}
        )
        log = _RecordingLog()

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=log,
            attempt_source="scheduler",
        )

        assert log.calls[0]["outcome"] == "blocked"
        assert "202" in log.calls[0]["detail"]
        # Nothing was extracted, so there is no tier — None, not "unknown".
        assert log.calls[0]["extraction_source"] is None

    @pytest.mark.asyncio
    async def test_dead_page_is_recorded_as_fetch_failed(self) -> None:
        ps, product, store, session, fetcher, parser = _fixtures()
        fetcher.fetch = AsyncMock(return_value={"ok": False, "error": "HTTP 404"})
        log = _RecordingLog()

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=log,
            attempt_source="manual-check",
        )

        assert log.calls[0]["outcome"] == "fetch_failed"
        assert log.calls[0]["source"] == "manual-check"

    @pytest.mark.asyncio
    async def test_page_without_a_price_is_recorded_as_no_price(self) -> None:
        """The store answered — that is a different fact from a wall, and it keeps its tier."""
        ps, product, store, session, fetcher, parser = _fixtures()
        parser.extract_price.return_value = _extraction(None, "discarded_low_confidence")
        log = _RecordingLog()

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
            attempt_log=log,
            attempt_source="scheduler",
        )

        assert log.calls[0]["outcome"] == "no_price"
        assert log.calls[0]["extraction_source"] == "discarded_low_confidence"

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_recorded_and_re_raised(self) -> None:
        """The scheduler's catch-all counted these; nothing durable ever said what they were."""
        ps, product, store, session, fetcher, parser = _fixtures()
        fetcher.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
        log = _RecordingLog()

        with pytest.raises(RuntimeError):
            await perform_price_check(
                product_store=ps,
                product=product,
                store=store,
                session=session,
                fetcher=fetcher,
                parser=parser,
                attempt_log=log,
                attempt_source="scheduler",
            )

        assert log.calls[0]["outcome"] == "error"
        assert "connection reset" in log.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_without_a_log_the_check_still_runs(self) -> None:
        """Telemetry is optional by construction — several hundred existing tests rely on it."""
        ps, product, store, session, fetcher, parser = _fixtures()
        parser.extract_price.return_value = _extraction("139.90", "jsonld")

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        assert outcome.success is True


class TestCheckAttemptLogNeverBreaksACheck:
    @pytest.mark.asyncio
    async def test_a_broken_database_is_swallowed(self) -> None:
        """A price check must not die because its telemetry could not be written."""

        def explode():
            raise RuntimeError("no database")

        log = CheckAttemptLog(explode)  # type: ignore[arg-type]

        await log.record(
            store_id=uuid.uuid4(),
            product_store_id=None,
            outcome="ok",
            source="scheduler",
        )  # must not raise


@pytest.mark.integration
class TestCheckAttemptsSurviveTheCallersTransaction:
    """Real Postgres: the two claims a mock cannot make."""

    async def _store(self, session) -> Store:
        return (await session.execute(select(Store).where(Store.slug == "apotea"))).scalar_one()

    async def _link(self, session, store: Store) -> ProductStore:
        product = Product(
            tenant_id=DEFAULT_TENANT_ID, name="Lambi", brand="Lambi", category=None, unit="st"
        )
        session.add(product)
        await session.flush()
        link = ProductStore(
            product_id=product.id,
            store_id=store.id,
            store_url=f"https://www.apotea.se/{uuid.uuid4()}",
            package_quantity=Decimal("24"),
        )
        session.add(link)
        await session.flush()
        await session.commit()
        return link

    @pytest.mark.asyncio
    async def test_the_row_lands_and_outlives_a_rolled_back_caller(
        self, db_session, session_factory
    ) -> None:
        store = await self._store(db_session)
        link = await self._link(db_session, store)
        # Held as plain values: rollback() EXPIRES every ORM instance, so reading link.id
        # afterwards would lazy-load and raise MissingGreenlet outside a greenlet context.
        store_id, link_id = store.id, link.id

        log = CheckAttemptLog(session_factory)
        await log.record(
            store_id=store_id,
            product_store_id=link_id,
            outcome="blocked",
            source="manual-check",
            detail="blocked (HTTP 405)",
        )

        # The caller's own work is written and then thrown away — exactly what the interactive
        # endpoints do when they raise on a wall before their commit. The attempt must survive
        # what the caller's own row does not.
        db_session.add(
            PricePoint(
                product_store_id=link_id,
                price_sek=Decimal("1.00"),
                checked_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db_session.flush()
        await db_session.rollback()

        surviving_points = (
            (
                await db_session.execute(
                    select(PricePoint).where(PricePoint.product_store_id == link_id)
                )
            )
            .scalars()
            .all()
        )
        assert surviving_points == []

        rows = (
            (
                await db_session.execute(
                    select(CheckAttempt).where(CheckAttempt.product_store_id == link_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].outcome == "blocked"
        assert rows[0].detail == "blocked (HTTP 405)"
        assert rows[0].checked_at is not None

    @pytest.mark.asyncio
    async def test_deleting_a_link_keeps_the_store_level_history(
        self, db_session, session_factory
    ) -> None:
        """ON DELETE SET NULL: removing a link must not fail on an audit row, and the store's
        reliability record is worth more than the association it loses."""
        store = await self._store(db_session)
        link = await self._link(db_session, store)
        store_id, link_id = store.id, link.id

        log = CheckAttemptLog(session_factory)
        await log.record(
            store_id=store_id, product_store_id=link_id, outcome="ok", source="scheduler"
        )

        await db_session.delete(link)
        await db_session.commit()

        stmt = select(CheckAttempt).where(CheckAttempt.store_id == store_id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].product_store_id is None
        assert rows[0].outcome == "ok"
