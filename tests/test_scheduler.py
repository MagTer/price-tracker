"""Tests for PriceCheckScheduler."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.deals import DealRow
from domain.result import PriceExtractionResult
from domain.schedule import MORNING_END_HOUR, MORNING_START_HOUR, STORE_TIMEZONE
from domain.scheduler import PriceCheckScheduler
from domain.service import PriceCheckOutcome


def _stockholm(naive_utc: datetime) -> datetime:
    """A naive-UTC result as Swedish wall-clock, for asserting on the förmiddag window."""
    return naive_utc.replace(tzinfo=UTC).astimezone(STORE_TIMEZONE)


def _make_extraction(
    price_sek: Decimal | None = Decimal("29.90"),
    offer_price_sek: Decimal | None = None,
    offer_type: str | None = None,
    store_unit_price_sek: Decimal | None = None,
    package_amount: Decimal | None = None,
    package_unit: str | None = None,
    pack_size: int | None = None,
    raw_response: dict[str, str | float | bool | None] | None = None,
) -> PriceExtractionResult:
    """Build a PriceExtractionResult for tests.

    `store_unit_price_sek` is what the STORE printed (D-05) — the scheduler never compares
    against it. `package_amount` / `package_unit` are the page's quantity EVIDENCE (D-08).
    """
    return PriceExtractionResult(
        price_sek=price_sek,
        store_unit_price_sek=store_unit_price_sek,
        offer_price_sek=offer_price_sek,
        offer_type=offer_type,
        offer_details=None,
        in_stock=True,
        confidence=0.95,
        pack_size=pack_size,
        package_amount=package_amount,
        package_unit=package_unit,
        raw_response=raw_response if raw_response is not None else {},
    )


def _make_product_store(
    store_slug: str = "willys",
    store_url: str = "https://www.willys.se/produkt/mjolk-100014716_ST",
    store_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    check_weekdays: list[int] | None = None,
    check_frequency_hours: int | None = None,
    package_quantity: Decimal | None = None,
    scraped_package_quantity: Decimal | None = None,
    product_unit: str | None = "st",
) -> MagicMock:
    """Create a mock ProductStore with nested product and store.

    package_quantity defaults to None — the D-02 state a link sits in until its first scrape.
    """
    ps = MagicMock()
    ps.id = uuid.uuid4()
    ps.store_id = store_id or uuid.uuid4()
    ps.product_id = product_id or uuid.uuid4()
    ps.store_url = store_url
    ps.check_weekdays = check_weekdays
    ps.check_frequency_hours = check_frequency_hours
    ps.last_checked_at = None
    ps.next_check_at = None
    ps.package_quantity = package_quantity
    ps.scraped_package_quantity = scraped_package_quantity

    product = MagicMock()
    product.name = "Mjolk Arla 1.5%"
    product.unit = product_unit
    ps.product = product

    store = MagicMock()
    store.name = "Willys"
    store.slug = store_slug
    # Real values, not MagicMock attributes: effective_schedule() reads these, and a
    # truthy MagicMock would silently masquerade as a weekday list.
    store.check_weekdays = None
    store.check_frequency_hours = 72
    ps.store = store

    return ps


def _make_scheduler(
    email_service: MagicMock | None = None,
) -> tuple[PriceCheckScheduler, MagicMock, AsyncMock]:
    """Build a scheduler with mocked dependencies.

    Returns (scheduler, session_factory_mock, fetcher_mock).
    """
    # Session factory returns an async context manager
    mock_session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=session_cm)

    fetcher: AsyncMock = AsyncMock()
    scheduler = PriceCheckScheduler(
        session_factory=session_factory,
        fetcher=fetcher,
        email_service=email_service,
    )
    return scheduler, session_factory, fetcher


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_get_status_returns_stats(self) -> None:
        """Initial status has expected keys and zero counters."""
        scheduler, _, _ = _make_scheduler()

        status = scheduler.get_status()

        assert status["running"] is False
        assert status["last_summary_date"] is None
        stats = status["stats"]
        assert isinstance(stats, dict)
        assert stats["checks_total"] == 0
        assert stats["checks_success"] == 0
        assert stats["checks_failed"] == 0
        assert stats["checks_api"] == 0
        assert stats["checks_llm"] == 0
        assert stats["alerts_sent"] == 0
        assert stats["summaries_sent"] == 0


class TestPause:
    """The auto-pause the add flows drive so a burst of manual adds doesn't race the
    scheduler into a store's WAF."""

    def test_status_exposes_paused_until(self) -> None:
        scheduler, _, _ = _make_scheduler()
        assert scheduler.get_status()["paused_until"] is None

        until = scheduler.pause_for(timedelta(hours=1))
        assert scheduler.get_status()["paused_until"] == until.isoformat()

    def test_pause_for_resets_the_clock_forward(self) -> None:
        scheduler, _, _ = _make_scheduler()
        first = scheduler.pause_for(timedelta(minutes=10))
        second = scheduler.pause_for(timedelta(hours=1))
        # Each call re-bases the window to now+duration — a later add pushes it further out.
        assert second > first

    @pytest.mark.asyncio
    async def test_paused_cycle_skips_without_touching_the_db(self) -> None:
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.pause_for(timedelta(hours=1))

        await scheduler._check_due_products()

        # Returned BEFORE opening a session — no query, so no store fetch either.
        session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_elapsed_pause_is_cleared_and_the_cycle_runs(self) -> None:
        scheduler, session_factory, _ = _make_scheduler()
        # A pause already in the past must not wedge the scheduler forever.
        scheduler._paused_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)

        result = MagicMock()
        result.unique.return_value.scalars.return_value.all.return_value = []
        session_factory.return_value.__aenter__.return_value.execute = AsyncMock(
            return_value=result
        )

        await scheduler._check_due_products()

        assert scheduler._paused_until is None
        session_factory.assert_called()  # proceeded to load due items


# ---------------------------------------------------------------------------
# _check_single_product
# ---------------------------------------------------------------------------


class TestCheckSingleProduct:
    @pytest.mark.asyncio
    async def test_check_single_product_success(self) -> None:
        """Happy path: fetch OK, parser returns price, PricePoint added."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction()

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store()

            await scheduler._check_single_product(product_store, mock_session)

        assert scheduler._stats["checks_total"] == 1
        assert scheduler._stats["checks_success"] == 1
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_single_product_fetch_fails(self) -> None:
        """Fetch returns ok=False: no PricePoint, stats not incremented."""
        scheduler, _, fetcher = _make_scheduler()

        fetcher.fetch = AsyncMock(return_value={"ok": False})
        mock_session = AsyncMock()
        product_store = _make_product_store()

        await scheduler._check_single_product(product_store, mock_session)

        assert scheduler._stats["checks_total"] == 1
        assert scheduler._stats["checks_success"] == 0
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_single_product_no_price_extracted(self) -> None:
        """Parser returns None price_sek: no PricePoint recorded."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction(price_sek=None)

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store()

            await scheduler._check_single_product(product_store, mock_session)

        assert scheduler._stats["checks_total"] == 1
        assert scheduler._stats["checks_success"] == 0
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_single_product_tracks_api_source(self) -> None:
        """raw_response source=willys_api increments checks_api."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction(raw_response={"source": "willys_api"})

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store()

            await scheduler._check_single_product(product_store, mock_session)

        assert scheduler._stats["checks_api"] == 1
        assert scheduler._stats["checks_llm"] == 0

    @pytest.mark.asyncio
    async def test_check_single_product_tracks_llm_source(self) -> None:
        """raw_response without willys_api source increments checks_llm."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction(raw_response={"source": "llm"})

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store()

            await scheduler._check_single_product(product_store, mock_session)

        assert scheduler._stats["checks_llm"] == 1
        assert scheduler._stats["checks_api"] == 0

    @pytest.mark.asyncio
    async def test_check_single_product_autofills_link_quantity(self) -> None:
        """Scrape write-path #1 honours D-07: fill the link's quantity when it has none."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction(
            price_sek=Decimal("139.90"),
            package_amount=Decimal("24"),
            package_unit="st",
        )

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store(package_quantity=None, product_unit="st")

            await scheduler._check_single_product(product_store, mock_session)

        assert product_store.package_quantity == Decimal("24")
        assert product_store.scraped_package_quantity == Decimal("24")

    @pytest.mark.asyncio
    async def test_check_single_product_never_overwrites_link_quantity(self) -> None:
        """D-07: the page is evidence, the typed value is intent. Evidence does not rewrite it."""
        scheduler, _, fetcher = _make_scheduler()
        extraction = _make_extraction(
            price_sek=Decimal("139.90"),
            package_amount=Decimal("12"),
            package_unit="st",
        )

        with patch.object(
            scheduler.parser, "extract_price", new_callable=AsyncMock, return_value=extraction
        ):
            fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
            mock_session = AsyncMock()
            product_store = _make_product_store(package_quantity=Decimal("24"), product_unit="st")

            await scheduler._check_single_product(product_store, mock_session)

        assert product_store.package_quantity == Decimal("24")  # untouched
        assert product_store.scraped_package_quantity == Decimal("12")  # the page's reading


# ---------------------------------------------------------------------------
# _check_alerts
# ---------------------------------------------------------------------------


def _make_watch(
    product_id: uuid.UUID | None = None,
    target_price_sek: Decimal | None = None,
    alert_on_any_offer: bool = False,
    price_drop_threshold_percent: int | None = None,
    unit_price_target_sek: Decimal | None = None,
    unit_price_drop_threshold_percent: int | None = None,
    last_alerted_at: datetime | None = None,
) -> MagicMock:
    """Create a minimal PriceWatch mock."""
    watch = MagicMock()
    watch.id = uuid.uuid4()
    watch.product_id = product_id or uuid.uuid4()
    watch.target_price_sek = target_price_sek
    watch.alert_on_any_offer = alert_on_any_offer
    watch.price_drop_threshold_percent = price_drop_threshold_percent
    watch.unit_price_target_sek = unit_price_target_sek
    watch.unit_price_drop_threshold_percent = unit_price_drop_threshold_percent
    watch.email_address = "user@example.com"
    watch.last_alerted_at = last_alerted_at
    watch.is_active = True
    return watch


class TestCheckAlerts:
    @pytest.mark.asyncio
    async def test_check_alerts_no_notifier_returns_early(self) -> None:
        """When notifier is None, no DB query is made and no alert is sent."""
        scheduler, _, _ = _make_scheduler(email_service=None)
        assert scheduler.notifier is None

        mock_session = AsyncMock()
        product_store = _make_product_store()
        extraction = _make_extraction(price_sek=Decimal("10.00"))

        await scheduler._check_alerts(product_store, extraction, mock_session)

        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_alerts_target_price_hit(self) -> None:
        """current_price <= target_price triggers alert."""
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        watch = _make_watch(
            target_price_sek=Decimal("30.00"),
            last_alerted_at=None,
        )

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store()
        extraction = _make_extraction(price_sek=Decimal("25.00"))

        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_called_once()
        assert scheduler._stats["alerts_sent"] == 1

    @pytest.mark.asyncio
    async def test_check_alerts_any_offer_trigger(self) -> None:
        """alert_on_any_offer=True and offer_type set triggers alert."""
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        watch = _make_watch(alert_on_any_offer=True, last_alerted_at=None)

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store()
        extraction = _make_extraction(
            price_sek=Decimal("29.90"),
            offer_price_sek=Decimal("19.90"),
            offer_type="kampanj",
        )

        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_called_once()
        assert scheduler._stats["alerts_sent"] == 1

    @pytest.mark.asyncio
    async def test_check_alerts_cooldown_respected(self) -> None:
        """last_alerted_at within 24h skips the alert."""
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        recent_alert = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=12)
        watch = _make_watch(
            target_price_sek=Decimal("30.00"),
            last_alerted_at=recent_alert,
        )

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store()
        extraction = _make_extraction(price_sek=Decimal("20.00"))

        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_not_called()
        assert scheduler._stats["alerts_sent"] == 0

    @pytest.mark.asyncio
    async def test_null_quantity_watch_fires_no_alert_and_does_not_crash(self) -> None:
        """A unit-price watch on a link with NO amount: no alert, no exception (D-02/MODEL-03).

        This is the failure mode the phase exists to prevent. `_check_alerts` is called DIRECTLY
        here on purpose: going through the outer loop would let the per-product `except Exception`
        in `_check_due_products` swallow a `None <= Decimal` TypeError, count it as checks_failed,
        and let this test pass over a scheduler that dies unattended at 06:00 every morning.
        """
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        # The watch would clear its target IF a unit price could be computed at all.
        watch = _make_watch(unit_price_target_sek=Decimal("10.00"), last_alerted_at=None)

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store(package_quantity=None)
        extraction = _make_extraction(price_sek=Decimal("139.90"))

        # No try/except: an exception here MUST fail this test, not be counted as a failed check.
        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_not_called()
        assert scheduler._stats["alerts_sent"] == 0

    @pytest.mark.asyncio
    async def test_null_quantity_still_fires_a_plain_target_price_alert(self) -> None:
        """The NULL guard must not disable the alert paths that never touch a unit price."""
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        watch = _make_watch(target_price_sek=Decimal("150.00"), last_alerted_at=None)

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store(package_quantity=None)
        extraction = _make_extraction(price_sek=Decimal("139.90"))

        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_called_once()
        # The computed unit price is None — the notifier simply renders no kr/enhet row.
        assert notifier_mock.send_price_alert.call_args.kwargs["unit_price_sek"] is None
        assert scheduler._stats["alerts_sent"] == 1

    @pytest.mark.asyncio
    async def test_unit_price_target_fires_on_computed_value(self) -> None:
        """The watch compares against the COMPUTED kr/unit, not against anything a store printed.

        24 rolls at 139.90 = 5.83 kr/rulle. A 6.00 target clears; a 5.00 target does not. The
        store's printed value (99.00 here — a deliberately absurd kr/kg-style number) is ignored.
        """
        for target, expect_alert in ((Decimal("6.00"), True), (Decimal("5.00"), False)):
            email_service = MagicMock()
            scheduler, _, _ = _make_scheduler(email_service=email_service)

            watch = _make_watch(unit_price_target_sek=target, last_alerted_at=None)

            mock_session = AsyncMock()
            scalar_result = MagicMock()
            scalar_result.scalars.return_value.all.return_value = [watch]
            mock_session.execute = AsyncMock(return_value=scalar_result)

            assert scheduler.notifier is not None
            notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
            notifier_mock.send_price_alert = AsyncMock(return_value=True)

            product_store = _make_product_store(package_quantity=Decimal("24"))
            extraction = _make_extraction(
                price_sek=Decimal("139.90"), store_unit_price_sek=Decimal("99.00")
            )

            await scheduler._check_alerts(product_store, extraction, mock_session)

            if expect_alert:
                notifier_mock.send_price_alert.assert_called_once()
                sent = notifier_mock.send_price_alert.call_args.kwargs["unit_price_sek"]
                assert sent is not None
                assert round(float(sent), 2) == 5.83
            else:
                notifier_mock.send_price_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_unit_price_drop_percent_uses_the_links_quantity(self) -> None:
        """The drop-% is computed from two computed unit prices — no back-derivation, no crash.

        Regular 159.90 / 24 = 6.66; offer 139.90 / 24 = 5.83 → a 12.5% drop clears a 10% threshold.
        The old code divided BY the scraped unit price, which is None on this extraction.
        """
        email_service = MagicMock()
        scheduler, _, _ = _make_scheduler(email_service=email_service)

        watch = _make_watch(unit_price_drop_threshold_percent=10, last_alerted_at=None)

        mock_session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalars.return_value.all.return_value = [watch]
        mock_session.execute = AsyncMock(return_value=scalar_result)

        assert scheduler.notifier is not None
        notifier_mock: MagicMock = scheduler.notifier  # type: ignore[assignment]
        notifier_mock.send_price_alert = AsyncMock(return_value=True)

        product_store = _make_product_store(package_quantity=Decimal("24"))
        extraction = _make_extraction(
            price_sek=Decimal("159.90"),
            offer_price_sek=Decimal("139.90"),
            store_unit_price_sek=None,  # the page printed none — legitimate after 04.1-02
        )

        await scheduler._check_alerts(product_store, extraction, mock_session)

        notifier_mock.send_price_alert.assert_called_once()
        drop = notifier_mock.send_price_alert.call_args.kwargs["unit_price_drop_percent"]
        assert drop is not None
        assert round(drop, 1) == 12.5


# ---------------------------------------------------------------------------
# _check_weekly_summary
# ---------------------------------------------------------------------------


class TestWeeklySummary:
    """The weekly buy-list email: due Monday after the förmiddag window (Swedish
    wall-clock via domain/schedule.py), sent to THE configured recipient — decoupled
    from the watches — with the WORSE deals filtered out before the notifier sees them."""

    RECIPIENT = "magnus@example.com"

    @staticmethod
    def _buy_row(
        verdict: str = "best",
        product_name: str = "Lambi Toalettpapper",
        store_name: str = "Willys",
        savings: float | None = 1.24,
        timing: str = "good",
        seen_cheaper: float | None = 0.0,
    ) -> DealRow:
        return DealRow(
            product_id=uuid.uuid4(),
            product_name=product_name,
            product_store_id=uuid.uuid4(),
            store_name=store_name,
            store_slug="willys",
            store_url="https://www.willys.se/produkt/x",
            package_size="24-pack",
            unit="st",
            price_sek=159.90,
            offer_price_sek=139.90,
            unit_price_sek=5.83,
            offer_type="kampanj",
            offer_details=None,
            checked_at=datetime(2026, 2, 16, 8, 0, 0),
            discount_percent=12.5,
            best_alt_unit_price_sek=7.07,
            best_alt_store="ICA",
            best_alt_package_size="8-pack",
            verdict=verdict,
            savings_per_unit_sek=savings,
            timing=timing,
            seen_cheaper_pct=seen_cheaper,
            lowest_unit_price_sek=5.83,
            lowest_seen_at=datetime(2026, 2, 9, 8, 0, 0),
            lowest_store="Willys",
        )

    @pytest.mark.asyncio
    async def test_weekly_summary_skips_non_monday(self) -> None:
        """Non-Monday weekday returns early without any DB queries."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        # Use a Tuesday at 15:00
        tuesday = datetime(2026, 2, 17, 15, 0, 0)  # weekday() == 1
        assert tuesday.weekday() == 1

        with patch("domain.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = tuesday

            await scheduler._check_weekly_summary()

        session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_weekly_summary_waits_for_the_swedish_window_end(self) -> None:
        """Monday 10:59 UTC in winter is 11:59 SWEDISH — the förmiddag window is still
        open and Monday's checks may not be in yet. No DB queries."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        monday_early = datetime(2026, 2, 16, 10, 59, 0)  # 11:59 Europe/Stockholm (CET)
        assert monday_early.weekday() == 0

        with patch("domain.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday_early

            await scheduler._check_weekly_summary()

        session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_weekly_summary_fires_at_swedish_noon_in_summer(self) -> None:
        """Monday 10:05 UTC in July is 12:05 SWEDISH — due. The old rule ("14:00" on
        naive UTC) would have sat on its hands until 16:00 Swedish summer time."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        summer_monday = datetime(2026, 7, 27, 10, 5, 0)
        assert summer_monday.weekday() == 0

        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[self._buy_row()]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = summer_monday
            await scheduler._check_weekly_summary()

        mock_send.assert_called_once()
        assert scheduler._last_summary_date == summer_monday.date()

    def _summary_session(
        self,
        session_factory: MagicMock,
        watches: list,
        link_rows_per_watch: list[list],
    ) -> AsyncMock:
        """Wire ONE session whose execute yields watches, then per-watch links.

        The deal rows no longer come through this session — the scheduler resolves them
        via domain.deals.current_deals, which tests patch at the scheduler's import site.
        The strict side_effect order doubles as the restart-guard-deletion assertion:
        if the old "recent alert within 10h" query ever comes back, it consumes the
        watches result and the flow derails immediately.
        """
        watches_result = MagicMock()
        watches_result.unique.return_value.scalars.return_value.all.return_value = watches

        results: list[MagicMock] = [watches_result]
        for link_rows in link_rows_per_watch:
            links_result = MagicMock()
            links_result.all.return_value = link_rows
            results.append(links_result)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=results)

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        session_factory.return_value = session_cm
        return mock_session

    @staticmethod
    def _watch(product_name: str = "Lambi Toalettpapper", unit: str | None = "st") -> MagicMock:
        watch = MagicMock()
        watch.product_id = uuid.uuid4()
        watch.email_address = "user@example.com"
        product = MagicMock()
        product.name = product_name
        product.unit = unit
        watch.product = product
        return watch

    @staticmethod
    def _link_row(
        price: str,
        quantity: str | None,
        store_name: str,
        offer: str | None = None,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        pp = MagicMock()
        pp.price_sek = Decimal(price)
        pp.offer_price_sek = Decimal(offer) if offer is not None else None
        pp.offer_type = None
        ps = MagicMock()
        ps.package_quantity = Decimal(quantity) if quantity is not None else None
        # An UNLABELED link: link_store_name must fall back to the chain name. A bare
        # MagicMock's auto-created store_label is truthy and would leak into the summary.
        ps.store_label = None
        store = MagicMock()
        store.name = store_name
        return pp, ps, store

    @pytest.mark.asyncio
    async def test_weekly_summary_sends_without_any_watch(self) -> None:
        """THE decoupling: deals alone earn the email, to the CONFIGURED recipient.

        The old sender derived its recipients from the watch rows, so a tracker with
        products but no alarm sent nothing — the digest existed only as a side effect
        of the watch feature."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[self._buy_row()]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["to_email"] == self.RECIPIENT
        assert [d.product_name for d in mock_send.call_args.kwargs["deals"]] == [
            "Lambi Toalettpapper"
        ]

    @pytest.mark.asyncio
    async def test_weekly_summary_excludes_worse_deals(self) -> None:
        """BEST + UNKNOWN ride; WORSE is information, not news — same split as the
        portal's 'Värt att köpa'."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        best = self._buy_row(verdict="best", product_name="Vinnare")
        unknown = self._buy_row(verdict="unknown", product_name="Ojämförbar", savings=None)
        worse = self._buy_row(verdict="worse", product_name="Förlorare", savings=-0.5)

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[best, unknown, worse]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        sent = mock_send.call_args.kwargs["deals"]
        assert [d.product_name for d in sent] == ["Vinnare", "Ojämförbar"]

    @pytest.mark.asyncio
    async def test_weekly_summary_excludes_a_poor_moment(self) -> None:
        """Cheapest link today is not enough. The prod case: a "2 för 130 kr" coffee that
        was BEST among its links while the same coffee had been 31 % cheaper nine days
        earlier. The email IS the buy list, so it must drop it exactly as the portal
        demotes it — otherwise Monday recommends what the page refuses to."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        good = self._buy_row(verdict="best", product_name="Vinnare")
        poor = self._buy_row(
            verdict="best", product_name="Bryggkaffe", timing="poor", seen_cheaper=31.3
        )

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch("domain.scheduler.current_deals", AsyncMock(return_value=[good, poor])),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        sent = mock_send.call_args.kwargs["deals"]
        assert [d.product_name for d in sent] == ["Vinnare"]

    @pytest.mark.asyncio
    async def test_weekly_summary_skips_without_recipient(self) -> None:
        """No SUMMARY_EMAIL and no ALLOWED_ENTRA_EMAIL: warn and stand down for the
        week — never crash, never guess an address."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", ""),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        mock_send.assert_not_called()
        session_factory.assert_not_called()
        assert scheduler._last_summary_date == monday.date()

    @pytest.mark.asyncio
    async def test_weekly_summary_sends_once_per_monday(self) -> None:
        """The in-memory dedup keys on the LOCAL Monday date — a second cycle the same
        afternoon must not send again."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[self._buy_row()]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()
            mock_dt.now.return_value = monday + timedelta(hours=2)
            await scheduler._check_weekly_summary()

        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_monday_is_not_stamped_as_sent(self) -> None:
        """"Nothing to send" at noon is often a WALL, not a fact: an ICA challenge on
        Monday morning defers every ICA link past 12:00, so the deals arrive later the
        same day. The empty branch must leave the dedup date unstamped and let the
        5-minute loop send as soon as there is something to say — stamping skipped the
        week's buy list exactly when the checks it waits for were delayed."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        deals_mock = AsyncMock(return_value=[])
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch("domain.scheduler.current_deals", deals_mock),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()  # nothing to say yet

            mock_send.assert_not_called()
            assert scheduler._last_summary_date is None

            # The walled morning's checks land; the next cycle has news.
            self._summary_session(session_factory, watches=[], link_rows_per_watch=[])
            deals_mock.return_value = [self._buy_row()]
            mock_dt.now.return_value = monday + timedelta(minutes=5)
            await scheduler._check_weekly_summary()

        mock_send.assert_called_once()
        assert scheduler._last_summary_date == monday.date()
        assert scheduler._stats["summaries_sent"] == 1

    @pytest.mark.asyncio
    async def test_weekly_summary_failure_is_not_counted_as_sent(self) -> None:
        """send_weekly_summary reports failure as a RETURN VALUE (ResendEmailService
        never raises) — a False must not stamp the dedup date, not count in the stats,
        and the next cycle the same Monday must retry. The old code discarded the bool:
        a transient Resend outage at noon lost the whole week's buy list while the log
        claimed success."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(side_effect=[False, True])
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[self._buy_row()]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

            assert scheduler._last_summary_date is None  # failure: no stand-down
            assert scheduler._stats["summaries_sent"] == 0

            # The retry is a full new pass — rewire the strict one-shot session.
            self._summary_session(session_factory, watches=[], link_rows_per_watch=[])
            mock_dt.now.return_value = monday + timedelta(minutes=5)
            await scheduler._check_weekly_summary()  # the retry succeeds

        assert mock_send.call_count == 2
        assert scheduler._last_summary_date == monday.date()
        assert scheduler._stats["summaries_sent"] == 1

    @pytest.mark.asyncio
    async def test_weekly_summary_exception_is_retried_the_same_monday(self) -> None:
        """A notifier that RAISES is the same story as one that returns False —
        logged, not stamped, retried while Monday lasts."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)
        self._summary_session(session_factory, watches=[], link_rows_per_watch=[])

        monday = datetime(2026, 2, 16, 15, 0, 0)
        assert scheduler.notifier is not None
        mock_send = AsyncMock(side_effect=RuntimeError("template bug"))
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch(
                "domain.scheduler.current_deals",
                AsyncMock(return_value=[self._buy_row()]),
            ),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        assert scheduler._last_summary_date is None
        assert scheduler._stats["summaries_sent"] == 0

    @pytest.mark.asyncio
    async def test_weekly_summary_reports_lowest_unit_price_across_links(self) -> None:
        """Lowest kr/enhet over the latest point per link — not the most recent link's price.

        24-pack at 139.90 = 5.83 kr/st beats 8-pack at 59.90 = 7.49 kr/st even though the
        8-pack is far cheaper in absolute kronor.
        """
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        monday = datetime(2026, 2, 16, 15, 0, 0)
        watch = self._watch()
        self._summary_session(
            session_factory,
            watches=[watch],
            link_rows_per_watch=[
                [
                    self._link_row("139.90", "24", "Willys"),
                    self._link_row("59.90", "8", "ICA"),
                ]
            ],
        )

        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch("domain.scheduler.current_deals", AsyncMock(return_value=[])),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        mock_send.assert_called_once()
        watched = mock_send.call_args.kwargs["watched_products"]
        assert watched == [
            {
                "name": "Lambi Toalettpapper",
                "lowest_price": Decimal("5.83"),
                "store_name": "Willys",
                "price_label": "kr/st",
            }
        ]
        assert scheduler._last_summary_date == monday.date()

    @pytest.mark.asyncio
    async def test_weekly_summary_uses_offer_as_effective_price(self) -> None:
        """The effective price (offer when present) feeds the kr/enhet comparison."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        monday = datetime(2026, 2, 16, 15, 0, 0)
        watch = self._watch()
        # Offer makes the 24-pack 119.90 -> 5.00 kr/st (link's regular would be 6.66).
        self._summary_session(
            session_factory,
            watches=[watch],
            link_rows_per_watch=[[self._link_row("159.90", "24", "Willys", offer="119.90")]],
        )

        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch("domain.scheduler.current_deals", AsyncMock(return_value=[])),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        watched = mock_send.call_args.kwargs["watched_products"]
        assert watched[0]["lowest_price"] == Decimal("5.00")

    @pytest.mark.asyncio
    async def test_weekly_summary_absolute_fallback_when_no_link_has_quantity(self) -> None:
        """No quantity anywhere -> lowest absolute effective price, labeled plain "kr"."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        monday = datetime(2026, 2, 16, 15, 0, 0)
        watch = self._watch()
        self._summary_session(
            session_factory,
            watches=[watch],
            link_rows_per_watch=[
                [
                    self._link_row("139.90", None, "Willys"),
                    self._link_row("129.00", None, "Coop"),
                ]
            ],
        )

        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
            patch("domain.scheduler.SUMMARY_EMAIL", self.RECIPIENT),
            patch("domain.scheduler.current_deals", AsyncMock(return_value=[])),
            patch.object(scheduler.notifier, "send_weekly_summary", mock_send),
        ):
            mock_dt.now.return_value = monday
            await scheduler._check_weekly_summary()

        watched = mock_send.call_args.kwargs["watched_products"]
        assert watched == [
            {
                "name": "Lambi Toalettpapper",
                "lowest_price": Decimal("129.00"),
                "store_name": "Coop",
                "price_label": "kr",
            }
        ]


# ---------------------------------------------------------------------------
# _check_due_products (session lifecycle)
# ---------------------------------------------------------------------------


class TestCheckDueProducts:
    @pytest.mark.asyncio
    async def test_throttles_each_item_through_the_shared_ledger(self) -> None:
        """Every due item is spaced via the injected rate limiter, keyed by store id.

        This is the scheduler half of Fix B: the politeness ledger is no longer a
        private dict but the shared StoreRateLimiter, so quick-add fetches count against
        the same per-store budget.
        """
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()

        ps1 = _make_product_store(store_id=uuid.uuid4())
        ps2 = _make_product_store(store_id=uuid.uuid4())

        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps1, ps2]

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock):
            await scheduler._check_due_products()

        assert scheduler.rate_limiter.acquire.await_count == 2
        keys = {call.args[0] for call in scheduler.rate_limiter.acquire.await_args_list}
        assert keys == {ps1.store_id, ps2.store_id}
        for call in scheduler.rate_limiter.acquire.await_args_list:
            assert call.args[1] == scheduler.RATE_LIMIT_DELAY

    @pytest.mark.asyncio
    async def test_each_item_gets_its_own_session(self) -> None:
        """One session loads due items; each item then gets a fresh session
        (rate-limit sleeps must never hold a DB connection)."""
        scheduler, session_factory, _ = _make_scheduler()

        ps1 = _make_product_store()
        ps2 = _make_product_store()

        # First session: due query. Later sessions: per-item work.
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps1, ps2]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            await scheduler._check_due_products()

        # 1 load session + 2 per-item sessions
        assert session_factory.call_count == 3
        assert mock_check.call_count == 2
        # Each per-item session committed its own transaction
        sessions[1].commit.assert_awaited_once()
        sessions[2].commit.assert_awaited_once()
        # The load session never commits (read-only)
        sessions[0].commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_item_does_not_stop_batch(self) -> None:
        """An exception on item 1 increments checks_failed; item 2 still runs."""
        scheduler, session_factory, _ = _make_scheduler()

        ps1 = _make_product_store()
        ps2 = _make_product_store()

        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps1, ps2]

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [RuntimeError("boom"), None]
            await scheduler._check_due_products()

        assert mock_check.call_count == 2
        assert scheduler._stats["checks_failed"] == 1

    @staticmethod
    def _update_params(execute_call) -> dict:
        """Compile the UPDATE statement an execute() received and return its params."""
        return execute_call.args[0].compile().params

    @pytest.mark.asyncio
    async def test_exception_advances_next_check_one_hour_via_own_session(self) -> None:
        """An exception-throwing link is backed off +1h so it stops hammering every 5 min."""
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store()
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        before = datetime.now(UTC).replace(tzinfo=None)
        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = RuntimeError("boom")
            await scheduler._check_due_products()
        after = datetime.now(UTC).replace(tzinfo=None)

        # load session + per-item session + the backoff's OWN session
        assert len(sessions) == 3
        backoff = sessions[2]
        backoff.execute.assert_awaited_once()
        backoff.commit.assert_awaited_once()
        params = self._update_params(backoff.execute.call_args)
        assert before + timedelta(hours=1) <= params["next_check_at"] <= after + timedelta(hours=1)
        # The backoff leaves last_checked_at alone — the check never happened.
        assert "last_checked_at" not in params
        # The per-item session never got to its schedule UPDATE.
        sessions[1].commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_weekday_check_retries_next_morning(self) -> None:
        """fetch_failed/no_price on a weekday link -> tomorrow's förmiddag, not next week.

        And not bare +24h: that kept the clock time the failure happened at, so a link
        that failed at 12:01 local retried at 12:01 forever — outside the 06–12 window
        the schedule exists to hold it in (prod: Hushållspapper, 404 at 12:01)."""
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=[0])
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        failed_outcome = MagicMock()
        failed_outcome.success = False
        # Explicit: a MagicMock auto-creates `.blocked` as a TRUTHY mock, which would send
        # this through the breaker's deferral instead of the +24h path under test. "Failed"
        # here means the fetch reached the store and yielded nothing — not a bot wall.
        failed_outcome.blocked = False

        before = datetime.now(UTC).replace(tzinfo=None)
        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = failed_outcome
            await scheduler._check_due_products()

        params = self._update_params(sessions[1].execute.call_args)
        retry_local = _stockholm(params["next_check_at"])
        assert retry_local.date() == (_stockholm(before) + timedelta(days=1)).date()
        assert MORNING_START_HOUR <= retry_local.hour < MORNING_END_HOUR
        sessions[1].commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_frequency_check_keeps_jittered_schedule(self) -> None:
        """Frequency-based links keep the jittered rescheduling on failure."""
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=None, check_frequency_hours=72)
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        failed_outcome = MagicMock()
        failed_outcome.success = False
        # Explicit: a MagicMock auto-creates `.blocked` as a TRUTHY mock, which would send
        # this through the breaker's deferral instead of the +24h path under test. "Failed"
        # here means the fetch reached the store and yielded nothing — not a bot wall.
        failed_outcome.blocked = False

        sentinel = datetime(2026, 3, 2, 8, 0, 0)
        with (
            patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check,
            patch(
                "domain.scheduler.next_check_time_for_link",
                new_callable=AsyncMock,
                return_value=sentinel,
            ) as mock_compute,
        ):
            mock_check.return_value = failed_outcome
            await scheduler._check_due_products()

        mock_compute.assert_called_once()
        params = self._update_params(sessions[1].execute.call_args)
        assert params["next_check_at"] == sentinel

    @pytest.mark.asyncio
    async def test_successful_weekday_check_keeps_weekday_schedule(self) -> None:
        """Success on a weekday link keeps the next-weekday morning-spread behavior."""
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=[0])
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        ok_outcome = MagicMock()
        ok_outcome.success = True
        ok_outcome.blocked = False  # see the note on failed_outcome.blocked above

        sentinel = datetime(2026, 3, 9, 7, 30, 0)
        with (
            patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check,
            patch(
                "domain.scheduler.next_check_time_for_link",
                new_callable=AsyncMock,
                return_value=sentinel,
            ) as mock_compute,
        ):
            mock_check.return_value = ok_outcome
            await scheduler._check_due_products()

        mock_compute.assert_called_once()
        params = self._update_params(sessions[1].execute.call_args)
        assert params["next_check_at"] == sentinel

    @pytest.mark.asyncio
    async def test_a_blocked_link_is_deferred_by_the_breaker_not_by_24h(self) -> None:
        """A wall is the STORE's state, not this link's failure — so the link that discovers
        it gets the same cooldown deferral every other due link of that store gets.

        It used to take +24h for being first in the ASC due queue while its siblings resumed
        in minutes, and +24h keeps the clock time the wall happened at: a link blocked at
        23:16 retried at 23:16 the next night, and the night after, permanently outside the
        06-12 förmiddag window the schedule exists to hold it in.
        """
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=[0])
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        blocked_outcome = MagicMock()
        blocked_outcome.success = False
        blocked_outcome.blocked = True

        before = datetime.now(UTC).replace(tzinfo=None)
        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = blocked_outcome
            await scheduler._check_due_products()
        after = datetime.now(UTC).replace(tzinfo=None)

        params = self._update_params(sessions[1].execute.call_args)
        # Deferred to the breaker's cooldown (base 5 min), NOT a day out.
        assert (
            before + timedelta(minutes=5) <= params["next_check_at"] <= after + timedelta(minutes=5)
        )

    @pytest.mark.asyncio
    async def test_a_wall_does_not_count_as_a_check(self) -> None:
        """last_checked_at is left alone when we never saw the page.

        Otherwise "Senast kollad" and the portal's freshness line report a block as fresh
        data — and the skip branch, which defers a cooling store's other links, already
        leaves the field alone. Same event, same bookkeeping.
        """
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=[0])
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        blocked_outcome = MagicMock()
        blocked_outcome.success = False
        blocked_outcome.blocked = True

        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = blocked_outcome
            await scheduler._check_due_products()

        params = self._update_params(sessions[1].execute.call_args)
        assert "last_checked_at" not in params

    @pytest.mark.asyncio
    async def test_a_reached_store_with_no_price_still_counts_as_a_check(self) -> None:
        """The counterpart: the page loaded and yielded nothing extractable. The store
        answered, which is what last_checked_at means — and what keeps the +24h retry."""
        scheduler, session_factory, _ = _make_scheduler()

        ps = _make_product_store(check_weekdays=[0])
        due_result = MagicMock()
        due_result.unique.return_value.scalars.return_value.all.return_value = [ps]

        sessions: list[AsyncMock] = []

        def make_session_cm():
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=due_result)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            sessions.append(mock_session)
            return cm

        session_factory.side_effect = lambda: make_session_cm()

        no_price = MagicMock()
        no_price.success = False
        no_price.blocked = False
        no_price.failure_reason = "no_price"

        before = datetime.now(UTC).replace(tzinfo=None)
        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = no_price
            await scheduler._check_due_products()

        params = self._update_params(sessions[1].execute.call_args)
        assert "last_checked_at" in params
        retry_local = _stockholm(params["next_check_at"])
        assert retry_local.date() == (_stockholm(before) + timedelta(days=1)).date()
        assert MORNING_START_HOUR <= retry_local.hour < MORNING_END_HOUR


class TestStoreCircuitBreaker:
    """A bot-wall answer cools the WHOLE store down so we stop poking a challenging WAF (A)."""

    def _due_session(self, session_factory, due_items) -> None:
        result = MagicMock()
        result.unique.return_value.scalars.return_value.all.return_value = due_items
        session_factory.return_value.__aenter__.return_value.execute = AsyncMock(
            return_value=result
        )

    @pytest.mark.asyncio
    async def test_block_trips_cooldown_and_skips_the_rest_of_the_store(self) -> None:
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        ps1 = _make_product_store(store_id=store_id)
        ps2 = _make_product_store(store_id=store_id)  # same store, second due link
        self._due_session(session_factory, [ps1, ps2])

        blocked = PriceCheckOutcome(
            success=False,
            failure_reason="fetch_failed",
            fetch_error="blocked (HTTP 202)",
            extraction=None,
            price_point=None,
            mismatch=None,
            blocked=True,
        )
        scheduler._check_single_product = AsyncMock(return_value=blocked)

        await scheduler._check_due_products()

        assert scheduler.block_registry.blocked_until(store_id) is not None
        # Only the FIRST link was fetched; the breaker skipped the second same-store link.
        assert scheduler._check_single_product.await_count == 1
        assert scheduler.rate_limiter.acquire.await_count == 1

    @pytest.mark.asyncio
    async def test_a_cooled_store_is_skipped_without_fetching(self) -> None:
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        scheduler.block_registry.record_block(store_id, store_name="ICA", source="test")
        self._due_session(session_factory, [_make_product_store(store_id=store_id)])
        scheduler._check_single_product = AsyncMock()

        await scheduler._check_due_products()

        scheduler._check_single_product.assert_not_awaited()
        scheduler.rate_limiter.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_elapsed_cooldown_is_cleared_and_the_store_checked(self) -> None:
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        scheduler.block_registry.record_block(store_id, store_name="ICA", source="test")
        # Rewind the cooldown so it has already elapsed.
        scheduler.block_registry._until[store_id] = datetime.now(UTC).replace(
            tzinfo=None
        ) - timedelta(minutes=1)
        self._due_session(session_factory, [_make_product_store(store_id=store_id)])
        ok = PriceCheckOutcome(
            success=True,
            failure_reason=None,
            fetch_error=None,
            extraction=_make_extraction(),
            price_point=None,
            mismatch=None,
        )
        scheduler._check_single_product = AsyncMock(return_value=ok)

        await scheduler._check_due_products()

        assert scheduler.block_registry.blocked_until(store_id) is None
        scheduler._check_single_product.assert_awaited_once()
        # A successful probe also resets the escalation, so the NEXT block starts at base again.
        assert scheduler.block_registry._strikes.get(store_id, 0) == 0

    @pytest.mark.asyncio
    async def test_a_no_price_page_still_resets_the_escalation(self) -> None:
        """ "Reached the store" is the predicate, NOT outcome.success: a page that loaded but
        yielded no extractable price proves the store is answering. The interactive paths
        (api/admin.py) already used this predicate; the scheduler once keyed on full success,
        so a store with a changed page layout kept its strike count forever — and the next
        real block escalated from a stale count instead of starting at base."""
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        # Leftover escalation from an earlier walled evening; the cooldown itself has lapsed.
        scheduler.block_registry._strikes[store_id] = 3
        self._due_session(session_factory, [_make_product_store(store_id=store_id)])
        no_price = PriceCheckOutcome(
            success=False,
            failure_reason="no_price",
            fetch_error=None,
            extraction=None,
            price_point=None,
            mismatch=None,
        )
        scheduler._check_single_product = AsyncMock(return_value=no_price)

        await scheduler._check_due_products()

        assert scheduler.block_registry._strikes.get(store_id, 0) == 0

    @pytest.mark.asyncio
    async def test_a_plain_fetch_failure_leaves_the_strikes_standing(self) -> None:
        """A non-wall fetch failure (transient 5xx, dead page) is NOT "the store answered" —
        it must neither trip the breaker nor clear the escalation."""
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        scheduler.block_registry._strikes[store_id] = 3
        self._due_session(session_factory, [_make_product_store(store_id=store_id)])
        failed = PriceCheckOutcome(
            success=False,
            failure_reason="fetch_failed",
            fetch_error="HTTP 503",
            extraction=None,
            price_point=None,
            mismatch=None,
            blocked=False,
        )
        scheduler._check_single_product = AsyncMock(return_value=failed)

        await scheduler._check_due_products()

        assert scheduler.block_registry._strikes.get(store_id, 0) == 3
        assert scheduler.block_registry.blocked_until(store_id) is None


class TestDeferralSpread:
    """Breaker deferrals must not stamp every link with the SAME timestamp.

    Identical deferrals meant the whole store came due at the instant the cooldown lapsed
    and drained on the ledger's 60 s floor, back to back — the burst pattern that walled
    ICA twice on 2026-07-29, and it left 3 links on the exact same microsecond in prod.
    """

    def _due_session(self, session_factory, due_items) -> AsyncMock:
        result = MagicMock()
        result.unique.return_value.scalars.return_value.all.return_value = due_items
        mock_session = session_factory.return_value.__aenter__.return_value
        mock_session.execute = AsyncMock(return_value=result)
        return mock_session

    @staticmethod
    def _deferral_times(mock_session) -> list[datetime]:
        """next_check_at from every UPDATE the session received (the first call is the
        due-items SELECT, which compiles without a next_check_at param)."""
        times = []
        for call in mock_session.execute.call_args_list:
            params = call.args[0].compile().params
            if "next_check_at" in params:
                times.append(params["next_check_at"])
        return times

    @pytest.mark.asyncio
    async def test_deferred_links_of_a_cooled_store_are_spread_apart(self) -> None:
        """Property, not exact timestamps: distinct, ordered, at least RATE_LIMIT_DELAY
        apart, and never more than delay+jitter apart — so the queue is already spaced
        when the cooldown expires."""
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        cooled_until = scheduler.block_registry.record_block(
            store_id, store_name="ICA", source="test"
        )
        due = [_make_product_store(store_id=store_id) for _ in range(3)]
        mock_session = self._due_session(session_factory, due)
        scheduler._check_single_product = AsyncMock()

        await scheduler._check_due_products()

        scheduler._check_single_product.assert_not_awaited()
        times = self._deferral_times(mock_session)
        assert len(times) == 3
        assert times[0] == cooled_until  # the first deferral IS the probe at expiry
        assert len(set(times)) == 3
        delay = PriceCheckScheduler.RATE_LIMIT_DELAY
        jitter = PriceCheckScheduler.RATE_LIMIT_JITTER
        for earlier, later in zip(times, times[1:], strict=False):
            gap = (later - earlier).total_seconds()
            assert delay <= gap <= delay + jitter

    @pytest.mark.asyncio
    async def test_two_cooled_stores_stagger_independently(self) -> None:
        """The stagger counter is per store — store B's first deferral starts at ITS
        cooldown expiry, not offset by store A's queue."""
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_a, store_b = uuid.uuid4(), uuid.uuid4()
        until_a = scheduler.block_registry.record_block(store_a, store_name="A", source="test")
        until_b = scheduler.block_registry.record_block(store_b, store_name="B", source="test")
        due = [
            _make_product_store(store_id=store_a),
            _make_product_store(store_id=store_b),
        ]
        mock_session = self._due_session(session_factory, due)
        scheduler._check_single_product = AsyncMock()

        await scheduler._check_due_products()

        times = self._deferral_times(mock_session)
        assert times == [until_a, until_b]

    @pytest.mark.asyncio
    async def test_discovering_link_and_skipped_siblings_share_the_stagger(self) -> None:
        """The link that discovers the wall takes the first slot (the probe at expiry);
        the skip branch spaces the store's other due links behind it — one shared
        counter, so they cannot collide with each other or with the prober."""
        scheduler, session_factory, _ = _make_scheduler()
        scheduler.rate_limiter = AsyncMock()
        store_id = uuid.uuid4()
        due = [_make_product_store(store_id=store_id) for _ in range(2)]
        mock_session = self._due_session(session_factory, due)

        blocked = PriceCheckOutcome(
            success=False,
            failure_reason="fetch_failed",
            fetch_error="blocked (HTTP 202)",
            extraction=None,
            price_point=None,
            mismatch=None,
            blocked=True,
        )
        scheduler._check_single_product = AsyncMock(return_value=blocked)

        await scheduler._check_due_products()

        blocked_until = scheduler.block_registry.blocked_until(store_id)
        assert blocked_until is not None
        times = self._deferral_times(mock_session)
        assert len(times) == 2
        assert times[0] == blocked_until  # the discoverer probes when the cooldown lapses
        gap = (times[1] - times[0]).total_seconds()
        delay = PriceCheckScheduler.RATE_LIMIT_DELAY
        assert delay <= gap <= delay + PriceCheckScheduler.RATE_LIMIT_JITTER
