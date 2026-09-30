"""One scheduler per database: a Postgres session-level advisory lock.

The scheduler runs in the web process, so every replica of the app would run one, and
two schedulers against one database check every store twice (a bot-wall risk at ICA)
and race the buy-list mail. The lock makes that impossible by construction rather than
by a replica count in a manifest: the process that holds it checks, the others serve the
portal and wait. It is the DATABASE's lock, so it is exactly as wide as the thing being
protected, and it needs no new component.

It does NOT keep two installations with separate databases apart (a lab copy beside
production): each database has its own lock. That is SCHEDULER_ENABLED's job.

A session-level lock lives as long as the connection that took it, so it is held on a
dedicated connection outside the request pool, checked every cycle, and released on
shutdown. If that connection dies, Postgres releases the lock with it, and the next
cycle here reconnects and competes again: a crashed leader is replaced within one cycle
without anything having to notice it crashed.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

# The key every instance of this app agrees on: an arbitrary constant (the bytes of
# "pricesc"). Nothing else in this database takes advisory locks, so any value works.
SCHEDULER_LOCK_KEY = 0x70726963657363


class PostgresLeaderLock:
    def __init__(self, engine: AsyncEngine, key: int = SCHEDULER_LOCK_KEY) -> None:
        self._engine = engine
        self._key = key
        self._conn: AsyncConnection | None = None

    async def acquire(self) -> bool:
        if self._conn is not None:
            try:
                # Still ours? The lock lives exactly as long as this connection.
                await self._conn.execute(text("SELECT 1"))
                return True
            except Exception as exc:  # noqa: BLE001 — a dead connection means a lost lock
                logger.warning(
                    "Leader lock connection lost (%s); competing again", type(exc).__name__
                )
                await self._discard(invalidate=True)
        # AUTOCOMMIT: the lock is session-level, and a transaction left open on a
        # connection held for the process's lifetime would pin the database's
        # vacuum horizon and read as "idle in transaction" forever.
        conn = await (await self._engine.connect()).execution_options(isolation_level="AUTOCOMMIT")
        try:
            got = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._key})
            ).scalar_one()
        except Exception:
            await conn.invalidate()
            await conn.close()
            raise
        if got:
            self._conn = conn
            return True
        await conn.close()
        return False

    async def release(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._key})
        except Exception as exc:  # noqa: BLE001 — closing the connection releases it anyway
            logger.warning(
                "Leader lock release failed (%s); closing the connection", type(exc).__name__
            )
        await self._discard()

    async def _discard(self, invalidate: bool = False) -> None:
        """Drop the held connection. `invalidate` for one that failed: it comes from the
        request pool, and a plain close() hands a dead connection back to it, where the next
        checkout (here, or a page load) fails on it. Found by the two-instance test, which
        failed 4 runs in 6 before this."""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                if invalidate:
                    await conn.invalidate()
                await conn.close()
            except Exception as exc:  # noqa: BLE001 — already dead is fine
                logger.debug("Leader lock connection close failed: %s", type(exc).__name__)
