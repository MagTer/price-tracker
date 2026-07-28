"""Abstract durable record of outgoing price checks — see infra.check_log.CheckAttemptLog."""

from typing import Protocol
from uuid import UUID


class ICheckAttemptLog(Protocol):
    """Records what every outgoing check produced, including the failures.

    A Protocol for the same reason the fetcher and the breaker are: perform_price_check lives
    in the domain and must not import a database adapter, and tests pass a stub (or nothing,
    which turns recording off).
    """

    async def record(
        self,
        *,
        store_id: UUID,
        product_store_id: UUID | None,
        outcome: str,
        source: str,
        extraction_source: str | None = None,
        duration_ms: int | None = None,
        detail: str | None = None,
    ) -> None:
        """Persist one attempt. MUST NOT raise: telemetry may never break a price check."""
        ...


__all__ = ["ICheckAttemptLog"]
