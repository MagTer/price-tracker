"""Protocol for the per-store request throttle (politeness ledger)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IRateLimiter(Protocol):
    """Abstract per-key request spacing — see infra.rate_limiter.StoreRateLimiter."""

    async def acquire(
        self, key: object, min_interval: float, *, max_wait: float | None = None
    ) -> float:
        """Block until it is polite to make a request to ``key``'s store.

        Args:
            key: Identifies the store whose request budget this spends (a store id).
            min_interval: Minimum seconds between successive requests to this store.
            max_wait: Optional cap on how long the caller will wait (for interactive
                callers that must not stall on a background reservation).

        Returns:
            The number of seconds actually slept before the slot opened.
        """
        ...


__all__ = ["IRateLimiter"]
