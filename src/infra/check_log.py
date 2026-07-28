"""Durable per-check telemetry: what every outgoing price check produced, failures included."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.models import CheckAttempt

logger = logging.getLogger(__name__)

# The failure text is evidence, not prose: keep the head, where the HTTP status lives.
_DETAIL_MAX = 255


class CheckAttemptLog:
    """Writes one ``check_attempts`` row per check, in its OWN transaction.

    The own-transaction part is the whole design, not a detail. The interactive endpoints
    raise their HTTPException on a failed or blocked check *before* they commit, so a row
    added to the caller's session is rolled back — and those are precisely the rows worth
    having. The scheduler commits either way, but it must not become the only path that
    records anything.

    It also MUST NOT raise. A price check that dies because its telemetry could not be
    written would trade the data for the thing the data is about; a warning is the correct
    failure mode. That makes a silently-empty table possible, which is why
    ``tests/test_check_log.py`` asserts the write against a real database rather than a mock.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        """Persist one attempt. Swallows every error — see the class docstring."""
        try:
            async with self._session_factory() as session:
                session.add(
                    CheckAttempt(
                        store_id=store_id,
                        product_store_id=product_store_id,
                        outcome=outcome,
                        source=source,
                        extraction_source=extraction_source,
                        duration_ms=duration_ms,
                        detail=detail[:_DETAIL_MAX] if detail else None,
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning("Could not record check attempt (%s/%s): %s", source, outcome, e)


__all__ = ["CheckAttemptLog"]
