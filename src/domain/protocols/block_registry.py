"""Protocol for the per-store circuit breaker."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class IBlockRegistry(Protocol):
    """Abstract per-store block state — see infra.store_block.StoreBlockRegistry."""

    def record_block(self, key: object, *, store_name: str = "", source: str = "") -> datetime:
        """Trip the breaker for this store; return when the cooldown lifts."""
        ...

    def record_success(self, key: object, *, store_name: str = "") -> None:
        """Clear the breaker and the escalation count for this store."""
        ...

    def blocked_until(self, key: object) -> datetime | None:
        """When this store's cooldown lifts, or None if it may be requested now."""
        ...

    def snapshot(self) -> list[dict[str, str | int]]:
        """Currently-blocked stores, for the status endpoint."""
        ...


__all__ = ["IBlockRegistry"]
