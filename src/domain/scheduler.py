"""Background scheduler for periodic price checks."""

import asyncio
import logging
import random
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from domain.models import (
    PricePoint,
    PriceWatch,
    Product,
    ProductStore,
    Store,
    link_store_name,
)
from domain.notifier import PriceNotifier
from domain.parser import PriceExtractionResult, PriceParser
from domain.pricing import unit_price_py
from domain.protocols import IEmailService, IFetcher
from domain.service import PriceCheckOutcome, perform_price_check

logger = logging.getLogger(__name__)


class PriceCheckScheduler:
    """Background scheduler for periodic price checks."""

    CHECK_INTERVAL_SECONDS = 300  # Check for due items every 5 minutes
    RATE_LIMIT_DELAY = 60.0  # Seconds between requests to same store
    BATCH_SIZE = 10  # Max items to check per cycle

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fetcher: IFetcher,
        email_service: IEmailService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.parser = PriceParser()
        # Create notifier wrapper if email service is provided
        self.notifier: PriceNotifier | None = None
        if email_service is not None:
            self.notifier = PriceNotifier(email_service)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_summary_date: date | None = None
        self._stats: dict[str, int] = {
            "checks_total": 0,
            "checks_success": 0,
            "checks_failed": 0,
            "checks_api": 0,
            "checks_jsonld": 0,
            "checks_llm": 0,
            "alerts_sent": 0,
            "summaries_sent": 0,
        }

    async def start(self) -> None:
        """Start the background scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Price check scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Price check scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_due_products()
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)

            try:
                await self._check_weekly_summary()
            except Exception as e:
                logger.error(f"Weekly summary error: {e}", exc_info=True)

            await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)

    async def _check_due_products(self) -> None:
        """Check all products that are due for a price check.

        Due items are loaded (with product/store eagerly joined) in one short
        session, which is then closed. Each item gets its own session so the
        per-store rate-limit sleeps never hold a DB connection, and one item's
        failure rolls back only its own transaction.
        """
        now = datetime.now(UTC).replace(tzinfo=None)

        async with self.session_factory() as session:
            # Find product-stores where:
            # 1. is_active = True
            # 2. next_check_at <= now (or NULL for backwards compatibility)
            stmt = (
                select(ProductStore)
                .options(
                    joinedload(ProductStore.product),
                    joinedload(ProductStore.store),
                )
                .where(
                    ProductStore.is_active.is_(True),
                    (ProductStore.next_check_at.is_(None) | (ProductStore.next_check_at <= now)),
                )
                .order_by(ProductStore.next_check_at.asc())
                .limit(self.BATCH_SIZE)
            )

            result = await session.execute(stmt)
            due_items = result.unique().scalars().all()

        if not due_items:
            logger.debug("No products due for price check")
            return

        logger.info(f"Checking {len(due_items)} products")

        last_store_id = None
        for product_store in due_items:
            try:
                # Rate limit per store (no session held during the sleep)
                if last_store_id == product_store.store_id:
                    await asyncio.sleep(self.RATE_LIMIT_DELAY)

                async with self.session_factory() as session:
                    outcome = await self._check_single_product(product_store, session)

                    # Update timestamps with the next check time via an explicit
                    # UPDATE — product_store is detached here. A FAILED check on a
                    # weekday-scheduled link retries in 24h instead of waiting a
                    # full week; frequency-based links keep their jittered
                    # schedule, and success keeps current behavior exactly.
                    now_utc = datetime.now(UTC).replace(tzinfo=None)
                    if (
                        outcome is not None
                        and not outcome.success
                        and product_store.check_weekday is not None
                    ):
                        next_check = now_utc + timedelta(hours=24)
                    else:
                        next_check = self._compute_next_check(product_store, now_utc)
                    await session.execute(
                        update(ProductStore)
                        .where(ProductStore.id == product_store.id)
                        .values(last_checked_at=now_utc, next_check_at=next_check)
                    )

                    await session.commit()

                last_store_id = product_store.store_id

            except Exception as e:
                logger.error(f"Failed to check product {product_store.id}: {e}")
                self._stats["checks_failed"] += 1
                # Without a backoff the link keeps next_check_at in the past,
                # stays FIRST in the ASC-ordered due queue, and gets hammered
                # every 5-minute cycle. Own short session + own try/except so a
                # dead DB cannot kill the loop.
                try:
                    async with self.session_factory() as backoff_session:
                        await backoff_session.execute(
                            update(ProductStore)
                            .where(ProductStore.id == product_store.id)
                            .values(
                                next_check_at=datetime.now(UTC).replace(tzinfo=None)
                                + timedelta(hours=1)
                            )
                        )
                        await backoff_session.commit()
                except Exception as backoff_error:
                    logger.error(
                        f"Failed to back off schedule for {product_store.id}: {backoff_error}"
                    )

    def _compute_next_check(self, product_store: ProductStore, now_utc: datetime) -> datetime:
        """Next check time: weekly on a weekday (morning spread) or jittered frequency."""
        if product_store.check_weekday is not None:
            # Weekday-based: schedule for next occurrence of that weekday
            # Spread checks over morning hours (06:00 - 12:00)
            days_until = (product_store.check_weekday - now_utc.weekday()) % 7
            if days_until == 0:
                # Already checked today, schedule for next week
                days_until = 7
            # Random hour between 6 and 12
            check_hour = 6 + int(random.random() * 6)  # noqa: S311
            check_minute = int(random.random() * 60)  # noqa: S311
            next_check = now_utc.replace(
                hour=check_hour, minute=check_minute, second=0, microsecond=0
            )
            return next_check + timedelta(days=days_until)

        # Frequency-based: use jitter as before
        jitter_percent = 0.1
        jitter_hours = (
            (random.random() * 2 - 1)  # noqa: S311
            * jitter_percent
            * product_store.check_frequency_hours
        )
        return now_utc + timedelta(hours=product_store.check_frequency_hours + jitter_hours)

    async def _check_single_product(
        self,
        product_store: ProductStore,
        session: AsyncSession,
    ) -> PriceCheckOutcome:
        """Check price for a single product-store combination.

        Thin wrapper around domain.service.perform_price_check — the single
        fetch → extract → enrich → apply-scrape → record flow. This method owns
        only the scheduler's bookkeeping (stats, alerts) and returns the outcome
        so the loop can reschedule failures.
        """
        self._stats["checks_total"] += 1
        logger.info(f"Checking price: {product_store.product.name} at {product_store.store.name}")

        outcome = await perform_price_check(
            product_store=product_store,
            product=product_store.product,
            store=product_store.store,
            session=session,
            fetcher=self.fetcher,
            parser=self.parser,
        )

        if outcome.failure_reason == "fetch_failed":
            logger.warning(f"Failed to fetch {product_store.store_url}")
            return outcome

        if outcome.failure_reason == "no_price":
            logger.warning(
                f"Could not extract price for {product_store.product.name} "
                f"at {product_store.store.name} - skipping"
            )
            return outcome

        # Track extraction source (API/JSON-LD vs LLM). An ENRICHED jsonld check
        # still counts as jsonld — enrichment keeps raw_response["source"] intact.
        extraction = outcome.extraction
        raw = extraction.raw_response or {}
        source = raw.get("source") if isinstance(raw, dict) else None
        if source == "willys_api":
            self._stats["checks_api"] += 1
        elif source == "jsonld":
            self._stats["checks_jsonld"] += 1
        else:
            self._stats["checks_llm"] += 1

        self._stats["checks_success"] += 1

        # Check for alerts — only when a point was actually recorded
        await self._check_alerts(product_store, extraction, session)

        return outcome

    async def _check_alerts(
        self,
        product_store: ProductStore,
        extraction: PriceExtractionResult,
        session: AsyncSession,
    ) -> None:
        """Check if price triggers any alerts."""
        if not self.notifier:
            return

        # Get active watches for this product
        stmt = select(PriceWatch).where(
            PriceWatch.product_id == product_store.product_id,
            PriceWatch.is_active.is_(True),
        )
        result = await session.execute(stmt)
        watches = result.scalars().all()

        current_price = extraction.offer_price_sek or extraction.price_sek
        if current_price is None:
            return

        # kr/unit is COMPUTED from the LINK's quantity (D-03) — never from anything a store
        # printed, whose definition varies per store (kr/rulle vs kr/pack vs kr/100g). The link
        # is a parameter of this method and _check_due_products joinedloads it: no extra query.
        #
        # A NULL package_quantity is a LEGITIMATE state (D-02) until the first scrape autofills
        # it, and it makes both of these None. Every comparison below therefore guards on
        # `is not None` EXPLICITLY. This is not decoration: a `None <= Decimal` TypeError raised
        # in here is swallowed by the per-product `except Exception` in _check_due_products,
        # logged as a failed check, and the operator never learns the watch stopped working.
        # The old code was only ACCIDENTALLY safe, via an `and extraction.unit_price_sek`
        # truthiness short-circuit. Deliberate now.
        package_quantity = product_store.package_quantity
        current_unit_price = unit_price_py(current_price, package_quantity)
        regular_unit_price = unit_price_py(extraction.price_sek, package_quantity)

        now = datetime.now(UTC).replace(tzinfo=None)

        for watch in watches:
            should_alert = False
            price_drop_percent = None
            unit_price_drop_percent = None

            # Check target price
            if watch.target_price_sek and current_price <= watch.target_price_sek:
                should_alert = True

            # Check for any offer
            if watch.alert_on_any_offer and extraction.offer_type:
                should_alert = True

            # Check price drop percentage
            if (
                watch.price_drop_threshold_percent
                and extraction.price_sek
                and extraction.offer_price_sek
            ):
                # Calculate percentage drop from regular price
                regular_price = extraction.price_sek
                current_price_value = extraction.offer_price_sek
                drop_percent = ((regular_price - current_price_value) / regular_price) * 100

                if drop_percent >= watch.price_drop_threshold_percent:
                    should_alert = True
                    price_drop_percent = float(drop_percent)

            # Check unit price target — against the COMPUTED value, with an explicit NULL guard.
            # A link that still needs an amount produces no alert and no crash.
            if (
                watch.unit_price_target_sek is not None
                and current_unit_price is not None
                and current_unit_price <= watch.unit_price_target_sek
            ):
                should_alert = True

            # Check unit price drop percentage. The old code back-derived a package size here
            # (dividing the offer price BY the scraped unit price) purely because the scheduler
            # had no quantity to work with — and that divisor can legitimately be None now that
            # the parser no longer synthesizes one. The quantity lives on the link, so the hack
            # is deleted rather than ported. Guard the divisor against BOTH None and zero.
            if (
                watch.unit_price_drop_threshold_percent
                and extraction.offer_price_sek is not None
                and current_unit_price is not None
                and regular_unit_price is not None
                and regular_unit_price != 0
            ):
                drop_percent_unit = (
                    (regular_unit_price - current_unit_price) / regular_unit_price
                ) * 100

                if drop_percent_unit >= watch.unit_price_drop_threshold_percent:
                    should_alert = True
                    unit_price_drop_percent = float(drop_percent_unit)

            # Don't spam - check last alerted time (24h cooldown)
            if should_alert and watch.last_alerted_at:
                cooldown = timedelta(hours=24)
                if (now - watch.last_alerted_at) < cooldown:
                    logger.debug(f"Skipping alert for watch {watch.id} - cooldown")
                    continue

            if should_alert:
                logger.info(f"Sending alert for watch {watch.id}")
                # Convert target_price to Decimal if present
                target_price_decimal = (
                    Decimal(str(watch.target_price_sek)) if watch.target_price_sek else None
                )
                success = await self.notifier.send_price_alert(
                    to_email=watch.email_address,
                    product_name=product_store.product.name,
                    store_name=link_store_name(product_store, product_store.store),
                    current_price=current_price,
                    target_price=target_price_decimal,
                    offer_type=extraction.offer_type,
                    offer_details=extraction.offer_details,
                    product_url=product_store.store_url,
                    price_drop_percent=price_drop_percent,
                    unit_price_sek=current_unit_price,
                    unit_price_drop_percent=unit_price_drop_percent,
                )

                if success:
                    watch.last_alerted_at = now
                    self._stats["alerts_sent"] += 1

    async def _check_weekly_summary(self) -> None:
        """Send weekly summary emails on Mondays at 14:00+."""
        if not self.notifier:
            return

        now = datetime.now(UTC).replace(tzinfo=None)

        # Only on Mondays, after 14:00
        if now.weekday() != 0 or now.hour < 14:
            return

        # Don't re-send if already sent today. This in-memory dedup is the ONLY
        # guard: the old "recent alert within 10h" restart heuristic silently
        # suppressed legitimate summaries (any Monday-morning alert killed that
        # afternoon's summary). A rare duplicate after a Monday-afternoon
        # restart is the accepted cost (locked decision).
        today = now.date()
        if self._last_summary_date == today:
            return

        logger.info("Sending weekly summary emails")

        async with self.session_factory() as session:
            cutoff = now - timedelta(days=7)

            # Get deals from last 7 days (price points with offers)
            deals_stmt = (
                select(PricePoint, ProductStore, Product, Store)
                .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                .join(Product, ProductStore.product_id == Product.id)
                .join(Store, ProductStore.store_id == Store.id)
                .where(
                    ProductStore.is_active.is_(True),
                    PricePoint.offer_price_sek.isnot(None),
                    PricePoint.checked_at >= cutoff,
                )
                .order_by(PricePoint.checked_at.desc())
            )
            deals_result = await session.execute(deals_stmt)
            deals_rows = deals_result.all()

            deals: list[dict[str, str | Decimal | None]] = []
            for price_point, _ps, product, store in deals_rows:
                deals.append(
                    {
                        "product_name": product.name,
                        "store_name": link_store_name(_ps, store),
                        "offer_price_sek": (
                            Decimal(str(price_point.offer_price_sek))
                            if price_point.offer_price_sek
                            else None
                        ),
                        "offer_type": price_point.offer_type,
                    }
                )

            # Get active watches with latest price per product
            watches_stmt = (
                select(PriceWatch)
                .where(PriceWatch.is_active.is_(True))
                .options(joinedload(PriceWatch.product))
            )
            watches_result = await session.execute(watches_stmt)
            watches = watches_result.unique().scalars().all()

            if not watches and not deals:
                logger.debug("No watches or deals - skipping weekly summary")
                self._last_summary_date = today
                return

            # Build watched products: the LOWEST kr/enhet across the product's links
            # (latest point per link), not "the latest point on the most-recently
            # checked link" the old ORDER BY checked_at DESC LIMIT 1 produced.
            watched_products: list[dict[str, str | Decimal | None]] = []
            product_ids_seen: set[str] = set()
            for watch in watches:
                pid = str(watch.product_id)
                if pid in product_ids_seen:
                    continue
                product_ids_seen.add(pid)

                # Latest point PER LINK (same shape as service.get_links_for_product).
                latest = (
                    select(
                        PricePoint.product_store_id.label("ps_id"),
                        func.max(PricePoint.checked_at).label("checked_at"),
                    )
                    .group_by(PricePoint.product_store_id)
                    .subquery()
                )
                links_stmt = (
                    select(PricePoint, ProductStore, Store)
                    .join(
                        latest,
                        (PricePoint.product_store_id == latest.c.ps_id)
                        & (PricePoint.checked_at == latest.c.checked_at),
                    )
                    .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                    .join(Store, ProductStore.store_id == Store.id)
                    .where(ProductStore.product_id == watch.product_id)
                )
                links_result = await session.execute(links_stmt)
                link_rows = links_result.all()

                # kr/enhet from domain.pricing — THE single definition. The effective
                # price is what you actually pay: the offer when there is one.
                best_unit: Decimal | None = None
                best_unit_store = ""
                best_abs: Decimal | None = None
                best_abs_store = ""
                for pp, ps, st in link_rows:
                    effective = (
                        pp.offer_price_sek if pp.offer_price_sek is not None else pp.price_sek
                    )
                    if effective is None:
                        continue
                    if best_abs is None or effective < best_abs:
                        best_abs = effective
                        best_abs_store = link_store_name(ps, st)
                    unit_price = unit_price_py(effective, ps.package_quantity)
                    if unit_price is not None and (best_unit is None or unit_price < best_unit):
                        best_unit = unit_price
                        best_unit_store = link_store_name(ps, st)

                cent = Decimal("0.01")
                if best_unit is not None:
                    lowest_price: Decimal | None = best_unit.quantize(cent, rounding=ROUND_HALF_UP)
                    store_name = best_unit_store
                    # The label carries the product's canonical unit ("kr/st").
                    unit = watch.product.unit or "enhet"
                    price_label = f"kr/{unit}"
                elif best_abs is not None:
                    # No link has a quantity yet: fall back to the lowest absolute
                    # effective price — labeled as such, never passed off as kr/enhet.
                    lowest_price = best_abs.quantize(cent, rounding=ROUND_HALF_UP)
                    store_name = best_abs_store
                    price_label = "kr"
                else:
                    lowest_price = None
                    store_name = ""
                    price_label = "kr"

                watched_products.append(
                    {
                        "name": watch.product.name,
                        "lowest_price": lowest_price,
                        "store_name": store_name,
                        "price_label": price_label,
                    }
                )

            # Group by email and send
            emails: set[str] = {w.email_address for w in watches}
            for email in emails:
                try:
                    await self.notifier.send_weekly_summary(
                        to_email=email,
                        deals=deals,
                        watched_products=watched_products,
                    )
                    self._stats["summaries_sent"] += 1
                    logger.info(f"Sent weekly summary to {email}")
                except Exception as e:
                    logger.error(f"Failed to send weekly summary to {email}: {e}")

        self._last_summary_date = today

    def get_status(self) -> dict[str, bool | str | date | dict[str, int] | None]:
        """Get scheduler status and statistics."""
        return {
            "running": self._running,
            "last_summary_date": (
                self._last_summary_date.isoformat() if self._last_summary_date else None
            ),
            "stats": dict(self._stats),
        }
