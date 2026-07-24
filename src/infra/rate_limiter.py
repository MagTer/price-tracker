"""Process-wide per-store request throttle — the politeness ledger.

THE single definition of "don't hit one store's servers too fast", shared by the
background scheduler (a 60 s cadence between checks of a store) AND the interactive
quick-add / manual-check fetches, which used to bypass all throttling and could
burst a store's WAF into rate-limiting us. That is exactly what happened with ICA's
CloudFront edge: a handful of requests within a few seconds started coming back as
HTTP 202 (empty challenge body) and 403 ("Request blocked"), which the fetcher then
could not turn into product data. Routing every caller through one ledger keyed by
store keeps quick-add's bursts and the scheduler's cadence on the same budget.

Reserve-a-slot algorithm: acquire() atomically claims the next free instant for a
store and then sleeps until it *outside* the lock, so concurrent callers to the same
store serialize into evenly spaced slots without ever holding a lock across the sleep.
"""

from __future__ import annotations

import asyncio
import time


class StoreRateLimiter:
    """Per-key minimum spacing between requests, safe under concurrent callers."""

    def __init__(self) -> None:
        # key -> monotonic instant the next request to that store may fire at.
        self._next_free: dict[object, float] = {}
        # key -> lock guarding that key's reservation (created lazily). A per-key lock
        # (not one global lock) lets requests to *different* stores reserve in parallel.
        self._locks: dict[object, asyncio.Lock] = {}

    def _lock_for(self, key: object) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def acquire(
        self, key: object, min_interval: float, *, max_wait: float | None = None
    ) -> float:
        """Block until it is polite to make a request to ``key``'s store; return slept secs.

        Reserves a slot at least ``min_interval`` seconds after the previous reservation
        for this key. ``max_wait`` caps how long an *interactive* caller will wait: when
        the next free slot is further out than ``max_wait`` (e.g. the scheduler just
        reserved a full 60 s), the caller waits only ``max_wait`` and takes its slot
        then — a rare scheduler/quick-add collision degrades to a short wait plus the
        fetcher's block-retry, not a minute-long UI stall. The store's future budget is
        never pulled *earlier* than already reserved, so cutting in never lets a later
        request fire sooner than the ledger intended.
        """
        async with self._lock_for(key):
            now = time.monotonic()
            prev_free = self._next_free.get(key, 0.0)
            start = max(now, prev_free)
            if max_wait is not None and start - now > max_wait:
                start = now + max_wait
            # max(prev_free, ...) so a capped interactive caller cannot rewind the clock.
            self._next_free[key] = max(prev_free, start + min_interval)

        delay = start - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        return max(0.0, delay)
