"""Tests for StoreRateLimiter — the shared per-store politeness ledger (Fix B).

The reserve-a-slot math is verified with a fake monotonic clock so no wall-clock time
passes; one real-concurrency test confirms that two callers to the same store actually
serialize.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

from infra.rate_limiter import StoreRateLimiter


class _Clock:
    """A controllable stand-in for time.monotonic()."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class TestSpacingMath:
    async def test_first_request_to_a_store_does_not_wait(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            delay = await limiter.acquire("store-a", 60)

        assert delay == 0
        sleep.assert_not_awaited()

    async def test_second_request_waits_the_interval(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await limiter.acquire("store-a", 60)
            delay = await limiter.acquire("store-a", 60)  # clock has not advanced

        assert delay == 60
        sleep.assert_awaited_once_with(60)

    async def test_different_stores_are_independent(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await limiter.acquire("store-a", 60)
            delay_b = await limiter.acquire("store-b", 60)

        assert delay_b == 0
        sleep.assert_not_awaited()

    async def test_max_wait_caps_an_interactive_callers_wait(self) -> None:
        """A quick-add caller never stalls longer than max_wait on a big reservation."""
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await limiter.acquire("store-a", 600)  # scheduler-style long reservation
            delay = await limiter.acquire("store-a", 5, max_wait=10)

        assert delay == 10  # capped, not the full 600
        sleep.assert_awaited_once_with(10)

    async def test_capped_caller_does_not_rewind_the_reservation(self) -> None:
        """Cutting in with max_wait must not let a later request fire earlier than reserved."""
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock),
        ):
            await limiter.acquire("store-a", 600)  # reserves up to t+600
            await limiter.acquire("store-a", 5, max_wait=10)  # cuts in at t+10
            # The long reservation still stands: a third caller waits the full ~600.
            delay = await limiter.acquire("store-a", 5)

        assert delay == 600


class TestRealConcurrency:
    async def test_concurrent_callers_to_same_store_serialize(self) -> None:
        """Two real coroutines hitting one store space out by the interval."""
        limiter = StoreRateLimiter()
        interval = 0.05

        start = time.monotonic()
        await asyncio.gather(
            limiter.acquire("store-a", interval),
            limiter.acquire("store-a", interval),
        )
        elapsed = time.monotonic() - start

        # One fires immediately, the other waits ~interval — so total >= ~interval.
        assert elapsed >= interval * 0.8

    async def test_concurrent_callers_to_different_stores_do_not_block(self) -> None:
        limiter = StoreRateLimiter()

        start = time.monotonic()
        await asyncio.gather(
            limiter.acquire("store-a", 0.05),
            limiter.acquire("store-b", 0.05),
        )
        elapsed = time.monotonic() - start

        assert elapsed < 0.05


class TestJitter:
    """Random spacing on top of the floor, so a store isn't hit on a clockwork beat."""

    async def test_jitter_extends_the_next_slot_by_the_random_amount(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as sleep,
            patch("infra.rate_limiter.random.uniform", return_value=7.0),
        ):
            await limiter.acquire("store-a", 60, jitter=30)  # reserves next slot at +67
            delay = await limiter.acquire("store-a", 60, jitter=30)  # clock frozen

        assert delay == 67  # the first reservation's jittered interval: 60 + 7
        sleep.assert_awaited_once_with(67)

    async def test_a_zero_draw_is_exactly_the_floor(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock),
            patch("infra.rate_limiter.random.uniform", return_value=0.0),
        ):
            await limiter.acquire("store-a", 60, jitter=30)
            delay = await limiter.acquire("store-a", 60, jitter=30)

        assert delay == 60  # jitter never shortens below min_interval

    async def test_default_no_jitter_never_calls_random(self) -> None:
        limiter = StoreRateLimiter()
        clock = _Clock()
        with (
            patch("infra.rate_limiter.time.monotonic", clock),
            patch("infra.rate_limiter.asyncio.sleep", new_callable=AsyncMock),
            patch("infra.rate_limiter.random.uniform") as uniform,
        ):
            await limiter.acquire("store-a", 60)
            delay = await limiter.acquire("store-a", 60)

        assert delay == 60
        uniform.assert_not_called()
