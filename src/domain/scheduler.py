"""Background scheduler for periodic price checks."""

import asyncio
import logging
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from domain.protocols import IEmailService, IFetcher
from domain.models import (
    PricePoint,
    PriceWatch,
    Product,
    ProductStore,
    Store,
)
from domain.notifier import PriceNotifier
from domain.parser import PriceExtractionResult, PriceParser

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
                    await self._check_single_product(product_store, session)

                    # Update timestamps with jittered next check time via an
                    # explicit UPDATE — product_store is detached here.
                    now_utc = datetime.now(UTC).replace(tzinfo=None)
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
    ) -> None:
        """Check price for a single product-store combination."""
        self._stats["checks_total"] += 1
        logger.info(f"Checking price: {product_store.product.name} at {product_store.store.name}")

        # Fetch page content
        fetch_result = await self.fetcher.fetch(product_store.store_url)
        if not fetch_result.get("ok"):
            logger.warning(f"Failed to fetch {product_store.store_url}")
            return

        # Extract price (store API -> JSON-LD -> LLM cascade)
        text_content = fetch_result.get("text", "")
        extraction = await self.parser.extract_price(
            text_content=text_content,
            store_slug=product_store.store.slug,
            product_name=product_store.product.name,
            store_url=product_store.store_url,
            html_content=fetch_result.get("html"),
        )

        # Skip recording if no price was extracted (required field)
        if extraction.price_sek is None:
            logger.warning(
                f"Could not extract price for {product_store.product.name} "
                f"at {product_store.store.name} - skipping"
            )
            return

        # Track extraction source (API/JSON-LD vs LLM)
        raw = extraction.raw_response or {}
        source = raw.get("source") if isinstance(raw, dict) else None
        if source == "willys_api":
            self._stats["checks_api"] += 1
        elif source == "jsonld":
            self._stats["checks_jsonld"] += 1
        else:
            self._stats["checks_llm"] += 1

        # Record price point
        price_point = PricePoint(
            product_store_id=product_store.id,
            price_sek=extraction.price_sek,
            unit_price_sek=extraction.unit_price_sek,
            offer_price_sek=extraction.offer_price_sek,
            offer_type=extraction.offer_type,
            offer_details=extraction.offer_details,
            in_stock=extraction.in_stock,
            raw_data=extraction.raw_response,
            checked_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(price_point)
        self._stats["checks_success"] += 1

        # Check for alerts
        await self._check_alerts(product_store, extraction, session)

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

        now = datetime.now(UTC).replace(tzinfo=None)

        for watch in watches:
            should_alert = False
            price_drop_percent = None
            unit_price_drop_percent = None
            current_unit_price = extraction.unit_price_sek

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

            # Check unit price target
            if watch.unit_price_target_sek and extraction.unit_price_sek:
                if extraction.unit_price_sek <= watch.unit_price_target_sek:
                    should_alert = True

            # Check unit price drop percentage
            if (
                watch.unit_price_drop_threshold_percent
                and extraction.unit_price_sek
                and extraction.price_sek
            ):
                # Calculate what regular unit price would be if there's an offer
                if extraction.offer_price_sek:
                    # units_in_package = offer_price / unit_price
                    units_in_package = extraction.offer_price_sek / extraction.unit_price_sek
                    # regular_unit_price = regular_price / units_in_package
                    regular_unit_price = extraction.price_sek / units_in_package
                    drop_percent_unit = (
                        (regular_unit_price - extraction.unit_price_sek) / regular_unit_price
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
                    store_name=product_store.store.name,
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

        # Don't re-send if already sent today
        today = now.date()
        if self._last_summary_date == today:
            return

        # Heuristic guard: check if any watch was alerted recently (within 10h)
        # This prevents duplicate summaries after restart
        async with self.session_factory() as session:
            recent_cutoff = now - timedelta(hours=10)
            recent_alert_stmt = (
                select(PriceWatch.id)
                .where(
                    PriceWatch.is_active.is_(True),
                    PriceWatch.last_alerted_at >= recent_cutoff,
                )
                .limit(1)
            )
            recent_result = await session.execute(recent_alert_stmt)
            if recent_result.scalar_one_or_none() is not None:
                logger.debug("Skipping weekly summary - recent alert found (restart guard)")
                self._last_summary_date = today
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
                        "store_name": store.name,
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

            # Build watched products with latest prices
            watched_products: list[dict[str, str | Decimal | None]] = []
            product_ids_seen: set[str] = set()
            for watch in watches:
                pid = str(watch.product_id)
                if pid in product_ids_seen:
                    continue
                product_ids_seen.add(pid)

                # Get latest price for this product
                latest_stmt = (
                    select(PricePoint, Store)
                    .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                    .join(Store, ProductStore.store_id == Store.id)
                    .where(ProductStore.product_id == watch.product_id)
                    .order_by(PricePoint.checked_at.desc())
                    .limit(1)
                )
                latest_result = await session.execute(latest_stmt)
                latest_row = latest_result.first()

                lowest_price: Decimal | None = None
                store_name = ""
                if latest_row:
                    pp, st = latest_row
                    price_val = pp.offer_price_sek or pp.price_sek
                    lowest_price = Decimal(str(price_val)) if price_val else None
                    store_name = st.name

                watched_products.append(
                    {
                        "name": watch.product.name,
                        "lowest_price": lowest_price,
                        "store_name": store_name,
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
