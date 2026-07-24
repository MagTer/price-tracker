"""Tests for PriceCheckScheduler."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.result import PriceExtractionResult
from domain.scheduler import PriceCheckScheduler


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
    async def test_weekly_summary_skips_before_14h(self) -> None:
        """Monday before 14:00 returns early without any DB queries."""
        email_service = MagicMock()
        scheduler, session_factory, _ = _make_scheduler(email_service=email_service)

        # Monday at 13:59
        monday_early = datetime(2026, 2, 16, 13, 59, 0)  # weekday() == 0
        assert monday_early.weekday() == 0

        with patch("domain.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday_early

            await scheduler._check_weekly_summary()

        session_factory.assert_not_called()

    def _summary_session(
        self,
        session_factory: MagicMock,
        deals_rows: list,
        watches: list,
        link_rows_per_watch: list[list],
    ) -> AsyncMock:
        """Wire ONE session whose execute yields deals, then watches, then per-watch links.

        The strict side_effect order doubles as the restart-guard-deletion assertion:
        if the old "recent alert within 10h" query ever comes back, it consumes the
        deals result and the flow derails immediately.
        """
        deals_result = MagicMock()
        deals_result.all.return_value = deals_rows

        watches_result = MagicMock()
        watches_result.unique.return_value.scalars.return_value.all.return_value = watches

        results = [deals_result, watches_result]
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
            deals_rows=[],
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
            deals_rows=[],
            watches=[watch],
            link_rows_per_watch=[[self._link_row("159.90", "24", "Willys", offer="119.90")]],
        )

        assert scheduler.notifier is not None
        mock_send = AsyncMock(return_value=True)
        with (
            patch("domain.scheduler.datetime") as mock_dt,
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
            deals_rows=[],
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
    async def test_failed_weekday_check_retries_in_24h(self) -> None:
        """fetch_failed/no_price on a weekday link -> +24h, not next week."""
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

        before = datetime.now(UTC).replace(tzinfo=None)
        with patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = failed_outcome
            await scheduler._check_due_products()
        after = datetime.now(UTC).replace(tzinfo=None)

        params = self._update_params(sessions[1].execute.call_args)
        next_check = params["next_check_at"]
        assert before + timedelta(hours=24) <= next_check <= after + timedelta(hours=24)
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

        sentinel = datetime(2026, 3, 2, 8, 0, 0)
        with (
            patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check,
            patch("domain.scheduler.next_check_time", return_value=sentinel) as mock_compute,
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

        sentinel = datetime(2026, 3, 9, 7, 30, 0)
        with (
            patch.object(scheduler, "_check_single_product", new_callable=AsyncMock) as mock_check,
            patch("domain.scheduler.next_check_time", return_value=sentinel) as mock_compute,
        ):
            mock_check.return_value = ok_outcome
            await scheduler._check_due_products()

        mock_compute.assert_called_once()
        params = self._update_params(sessions[1].execute.call_args)
        assert params["next_check_at"] == sentinel
