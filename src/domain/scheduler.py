"""Background scheduler for periodic price checks."""

import asyncio
import logging
import os
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from domain.deals import DEAL_WORSE, current_deals
from domain.models import (
    PricePoint,
    PriceWatch,
    ProductStore,
    Store,
    link_store_name,
)
from domain.notifier import PriceNotifier
from domain.parser import PriceExtractionResult, PriceParser
from domain.pricing import unit_price_py
from domain.protocols import (
    IBlockRegistry,
    ICheckAttemptLog,
    IEmailService,
    IFetcher,
    IRateLimiter,
)
from domain.result import extraction_source
from domain.schedule import (
    effective_schedule,
    next_check_time_for_link,
    next_morning_retry,
    weekly_summary_slot,
)
from domain.service import PriceCheckOutcome, perform_price_check

logger = logging.getLogger(__name__)

# The weekly summary recipient. Decoupled from the watches on purpose (v0.41.0): the
# buy-list email is the tracker's primary output, and deriving recipients from watch rows
# meant "no watches → no email" — the digest existed only as a side effect of the alarm
# feature. Falls back to the admin identity the app already knows (note: that is the
# Entra UPN, which for most tenants is a deliverable address — set SUMMARY_EMAIL when
# it is not).
SUMMARY_EMAIL = os.getenv("SUMMARY_EMAIL") or os.getenv("ALLOWED_ENTRA_EMAIL", "")


class PriceCheckScheduler:
    """Background scheduler for periodic price checks."""

    CHECK_INTERVAL_SECONDS = 300  # Check for due items every 5 minutes
    RATE_LIMIT_DELAY = 60.0  # Minimum seconds between requests to same store
    # Random 0..this added on top of RATE_LIMIT_DELAY so background checks don't hit a store
    # on a clockwork 60 s beat — even spacing is itself a mild bot tell. Background cadence has
    # slack to spare, so the extra wait costs nothing operationally.
    RATE_LIMIT_JITTER = 30.0
    BATCH_SIZE = 10  # Max items to check per cycle

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fetcher: IFetcher,
        email_service: IEmailService | None = None,
        rate_limiter: IRateLimiter | None = None,
        block_registry: IBlockRegistry | None = None,
        attempt_log: ICheckAttemptLog | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.parser = PriceParser()
        # THE politeness ledger. Injected in prod (app.py passes the process-wide
        # singleton) so background checks share a store's throttle budget with the
        # interactive quick-add fetches; a private one when omitted keeps tests isolated.
        if rate_limiter is None:
            from infra.rate_limiter import StoreRateLimiter

            rate_limiter = StoreRateLimiter()
        self.rate_limiter = rate_limiter
        # THE circuit breaker, shared for the same reason the ledger is: a bot wall found by an
        # interactive fetch must silence background checks too, and vice versa. Injected in prod
        # (app.py passes the process-wide singleton); a private one when omitted isolates tests.
        if block_registry is None:
            from infra.store_block import StoreBlockRegistry

            block_registry = StoreBlockRegistry()
        self.block_registry = block_registry
        # THE durable per-check record (check_attempts). None in tests = recording off; app.py
        # passes the process-wide instance. Nothing here depends on it, by design: telemetry
        # must never be able to stop a check.
        self.attempt_log = attempt_log
        # Create notifier wrapper if email service is provided
        self.notifier: PriceNotifier | None = None
        if email_service is not None:
            self.notifier = PriceNotifier(email_service)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # When set, background checks are skipped until this time (auto-cleared once it passes).
        # The add flows push it forward so a burst of manual adds does not race the scheduler
        # into a store's WAF. See pause_for / _check_due_products.
        self._paused_until: datetime | None = None
        self._last_summary_date: date | None = None
        self._stats: dict[str, int] = {
            "checks_total": 0,
            "checks_success": 0,
            "checks_failed": 0,
            "checks_api": 0,
            "checks_page_state": 0,
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

    def pause_for(self, duration: timedelta) -> datetime:
        """Silence background checks until now + duration; each call resets the clock.

        The add flows call this so a burst of manual quick-adds — each of which also fetches for
        its preview and leaves a due link the scheduler would grab — does not race the scheduler
        into a store's WAF. No manual resume: checks restart on the first cycle after the window
        lapses (see _check_due_products). In-memory on purpose (single-process app); a restart
        just resumes, which is harmless.
        """
        self._paused_until = datetime.now(UTC).replace(tzinfo=None) + duration
        logger.info("Scheduler paused until %s (background checks held)", self._paused_until)
        return self._paused_until

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

        if self._paused_until is not None:
            if now < self._paused_until:
                logger.debug("Scheduler paused until %s — skipping cycle", self._paused_until)
                return
            logger.info("Scheduler pause elapsed — resuming background checks")
            self._paused_until = None

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

        # Per-cycle stagger for breaker deferrals, keyed by store. Recreated every cycle,
        # so repeated cycles against a still-cooled store re-spread from zero instead of
        # marching the offsets outward — see _next_deferral_offset.
        deferral_offsets: dict[uuid.UUID, float] = {}

        for product_store in due_items:
            # Per-store circuit breaker: if this store block-throttled us recently, skip its
            # links WITHOUT fetching — poking a WAF that's challenging us only reinforces the
            # flag — and defer them past the cooldown so they leave the front of the ASC due
            # queue instead of starving other stores.
            cooled_until = self.block_registry.blocked_until(product_store.store_id)
            if cooled_until is not None:
                logger.info(
                    "Store %s cooling down until %s — skipping %s (%s) without fetching",
                    product_store.store.name,
                    cooled_until,
                    product_store.id,
                    product_store.product.name,
                )
                try:
                    async with self.session_factory() as skip_session:
                        await skip_session.execute(
                            update(ProductStore)
                            .where(ProductStore.id == product_store.id)
                            .values(
                                next_check_at=cooled_until
                                + self._next_deferral_offset(deferral_offsets, product_store)
                            )
                        )
                        await skip_session.commit()
                except Exception as skip_error:
                    logger.error("Failed to defer %s: %s", product_store.id, skip_error)
                continue

            try:
                # Rate limit per store (no session held during the sleep): keep at least
                # RATE_LIMIT_DELAY between requests to one store, whenever the previous
                # one happened — earlier in this batch, in a previous cycle, OR from an
                # interactive quick-add fetch, which now shares this same ledger.
                await self.rate_limiter.acquire(
                    product_store.store_id,
                    self.RATE_LIMIT_DELAY,
                    jitter=self.RATE_LIMIT_JITTER,
                )

                async with self.session_factory() as session:
                    outcome = await self._check_single_product(product_store, session)

                    now_utc = datetime.now(UTC).replace(tzinfo=None)

                    # A bot-wall answer trips the breaker for the WHOLE store: its other due
                    # links this cycle hit the skip above and stand down, turning "poke all N
                    # links during a challenge" into a single probe. (B already cut each blocked
                    # fetch from 3 requests to 1.) The breaker is the SHARED registry, so the
                    # interactive paths stand down too — and each consecutive block doubles the
                    # cooldown, so a store that keeps walling us is left alone for longer.
                    blocked_until: datetime | None = None
                    if outcome is not None and outcome.blocked:
                        blocked_until = self.block_registry.record_block(
                            product_store.store_id,
                            store_name=product_store.store.name,
                            source="scheduler",
                        )
                    elif outcome is not None and outcome.failure_reason != "fetch_failed":
                        # "Reached the store" ends the escalation — NOT outcome.success: a page
                        # that loaded fine but yielded no extractable price still proves the
                        # store is answering us. Same predicate as the interactive paths in
                        # api/admin.py (_record_store_outcome callers) — the breaker is shared
                        # state, so its callers must agree on what counts as a success.
                        self.block_registry.record_success(
                            product_store.store_id, store_name=product_store.store.name
                        )

                    # Update timestamps with the next check time via an explicit
                    # UPDATE — product_store is detached here. A FAILED check on a
                    # weekday-scheduled link retries the next morning instead of waiting a
                    # full week; frequency-based links keep their jittered schedule, and
                    # success reschedules through the shared slot-stratified definition.
                    #
                    # A BLOCK is not a failure of this link — it is the store walling
                    # everyone, and the breaker already owns that case with a measured,
                    # escalating cooldown. Deferring to `blocked_until` is exactly what the
                    # skip branch above gives every OTHER due link of that store; without
                    # this branch the one link that happened to sit first in the ASC due
                    # queue took a 24h penalty for discovering the wall while its siblings
                    # resumed in minutes. The discovering link takes the FIRST stagger slot
                    # (offset zero — it is the natural probe when the cooldown lapses); the
                    # skip branch spaces its siblings behind it, because identical deferral
                    # timestamps drain through the ledger as the back-to-back one-per-minute
                    # burst that walled ICA twice on 2026-07-29.
                    weekdays, _ = effective_schedule(product_store, product_store.store)
                    if blocked_until is not None:
                        next_check = blocked_until + self._next_deferral_offset(
                            deferral_offsets, product_store
                        )
                    elif outcome is not None and not outcome.success and weekdays:
                        # Tomorrow's förmiddag, not now+24h: the bare +24h kept the clock
                        # time the failure happened at, so a link that failed at 12:01
                        # local retried at 12:01 — outside the window the schedule exists
                        # to hold it in — every day until an attempt landed (the same walk
                        # v0.29.2 stopped for blocked checks).
                        next_check = next_morning_retry(now_utc)
                    else:
                        next_check = await next_check_time_for_link(
                            session, product_store, product_store.store, now_utc
                        )

                    # A wall is not a check: we never saw the page. Leaving last_checked_at
                    # alone keeps "Senast kollad" and the portal's freshness line honest
                    # (they would otherwise report a block as fresh data) and matches the
                    # skip branch above, which defers without touching it. A fetch that
                    # REACHED the store and yielded no price still counts — the store
                    # answered, which is what the field means.
                    values: dict[str, Any] = {"next_check_at": next_check}
                    if blocked_until is None:
                        values["last_checked_at"] = now_utc
                    await session.execute(
                        update(ProductStore)
                        .where(ProductStore.id == product_store.id)
                        .values(values)
                    )

                    await session.commit()

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

    def _next_deferral_offset(
        self, offsets: dict[uuid.UUID, float], product_store: ProductStore
    ) -> timedelta:
        """The next stagger offset for a link deferred to a store's cooldown expiry.

        Every deferred link used to get the SAME timestamp (the cooldown expiry), so when
        the cooldown lapsed they all came due at once and drained on the ledger's 60 s
        floor — back to back, which is exactly the burst pattern that provoked the wall in
        the first place. Each deferral in a cycle now lands at least RATE_LIMIT_DELAY
        after the previous one, plus jitter, so the queue is already spread when the
        cooldown expires. The dict is per-cycle (created in _check_due_products), which
        bounds the spread at BATCH_SIZE slots and means a store still cooled next cycle
        gets re-spread from zero instead of pushed further and further out.
        """
        current = offsets.get(product_store.store_id, 0.0)
        offsets[product_store.store_id] = (
            current + self.RATE_LIMIT_DELAY + random.uniform(0, self.RATE_LIMIT_JITTER)  # noqa: S311
        )
        return timedelta(seconds=current)

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
            attempt_log=self.attempt_log,
            attempt_source="scheduler",
        )

        if outcome.failure_reason == "fetch_failed":
            # Say WHICH kind of failure. A bot wall and a dead page both used to log the same
            # "Failed to fetch <url>", so reading back an evening of ICA logs could not tell
            # "they are challenging us" from "that product page is gone" — opposite responses.
            if outcome.blocked:
                logger.warning(
                    "Blocked while checking %s at %s: %s",
                    product_store.product.name,
                    product_store.store.name,
                    outcome.fetch_error,
                )
            else:
                logger.warning(
                    "Failed to fetch %s (%s at %s): %s",
                    product_store.store_url,
                    product_store.product.name,
                    product_store.store.name,
                    outcome.fetch_error,
                )
            return outcome

        if outcome.failure_reason == "no_price":
            logger.warning(
                f"Could not extract price for {product_store.product.name} "
                f"at {product_store.store.name} - skipping"
            )
            return outcome

        # Track extraction source (API/JSON-LD vs LLM). An ENRICHED jsonld check
        # still counts as jsonld — enrichment keeps raw_response["source"] intact.
        #
        # The store-HTML tier gets its OWN counter: "rusta_page"/"clasohlson_page" used to fall
        # through to the else and count as LLM checks, so every Rusta and Clas Ohlson check
        # inflated the one number anybody reads to judge what the cascade costs — while those
        # tiers call no model at all. These counters are per-process bookkeeping; the durable
        # per-check record is the check_attempts table.
        extraction = outcome.extraction
        source = extraction_source(extraction)
        if source == "willys_api":
            self._stats["checks_api"] += 1
        elif source.endswith("_page"):
            self._stats["checks_page_state"] += 1
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
        """Send THE weekly buy-list email once per Monday, after the förmiddag window.

        Timing comes from domain/schedule.py (weekly_summary_slot): Monday from 12:00
        Swedish wall-clock — the first moment the week's Monday checks (ICA and Willys
        both) are in. The old rule was "Monday 14:00" applied on naive UTC, i.e. 15/16
        Swedish depending on DST — the same silent drift v0.33.0 removed from the check
        window.
        """
        if not self.notifier:
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        slot = weekly_summary_slot(now)
        if slot is None:
            return

        # Don't re-send for the same (local) Monday. This in-memory dedup is the ONLY
        # guard: the old "recent alert within 10h" restart heuristic silently
        # suppressed legitimate summaries (any Monday-morning alert killed that
        # afternoon's summary). A rare duplicate after a Monday-afternoon
        # restart is the accepted cost (locked decision).
        if self._last_summary_date == slot:
            return

        # The recipient is configuration, not the union of watch emails: the summary
        # must arrive even when — especially when — no per-product alarm exists.
        if not SUMMARY_EMAIL:
            logger.warning(
                "Weekly summary due but no recipient configured "
                "(SUMMARY_EMAIL / ALLOWED_ENTRA_EMAIL) - skipping"
            )
            self._last_summary_date = slot
            return

        logger.info("Sending weekly summary email")

        async with self.session_factory() as session:
            # The buy list: BEST + UNKNOWN, the same split as the portal's "Värt att
            # köpa" — an offer another link beats per unit is information, not news,
            # and has no place in an email that IS the shopping decision. THE verdict
            # comes from domain/deals.py; grouping per butik is the notifier's job.
            deals = [d for d in await current_deals(session) if d.verdict != DEAL_WORSE]
            watched_products = await self._watched_products_summary(session)

            if not deals and not watched_products:
                logger.debug("No deals or watched products - skipping weekly summary")
                self._last_summary_date = slot
                return

            try:
                await self.notifier.send_weekly_summary(
                    to_email=SUMMARY_EMAIL,
                    deals=deals,
                    watched_products=watched_products,
                )
                self._stats["summaries_sent"] += 1
                logger.info(f"Sent weekly summary to {SUMMARY_EMAIL}")
            except Exception as e:
                logger.error(f"Failed to send weekly summary to {SUMMARY_EMAIL}: {e}")

        self._last_summary_date = slot

    async def _watched_products_summary(
        self, session: AsyncSession
    ) -> list[dict[str, str | Decimal | None]]:
        """The watched-products section: current lowest kr/enhet per bevakad produkt."""
        watches_stmt = (
            select(PriceWatch)
            .where(PriceWatch.is_active.is_(True))
            .options(joinedload(PriceWatch.product))
        )
        watches_result = await session.execute(watches_stmt)
        watches = watches_result.unique().scalars().all()

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
                effective = pp.offer_price_sek if pp.offer_price_sek is not None else pp.price_sek
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

        return watched_products

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status and statistics."""
        return {
            "running": self._running,
            "paused_until": self._paused_until.isoformat() if self._paused_until else None,
            # Which stores are currently walled off, and how deep into the escalation they are.
            # Without this the only evidence of a live block is a log line that scrolls away.
            "blocked_stores": self.block_registry.snapshot(),
            "last_summary_date": (
                self._last_summary_date.isoformat() if self._last_summary_date else None
            ),
            "stats": dict(self._stats),
        }
