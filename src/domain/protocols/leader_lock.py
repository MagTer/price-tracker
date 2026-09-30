"""Protocol for the scheduler's leader lock."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ILeaderLock(Protocol):
    """Only the holder runs background work — see infra.leader_lock.PostgresLeaderLock."""

    async def acquire(self) -> bool:
        """Take the lock, or confirm it is still held. False = another instance holds it."""
        ...

    async def release(self) -> None:
        """Give the lock up, so a standby can take over at once rather than on a timeout."""
        ...


__all__ = ["ILeaderLock"]
