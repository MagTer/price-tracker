"""Process-wide per-store circuit breaker — THE record of "this store is currently walling us".

The companion to `infra.rate_limiter`. The ledger answers *how fast* we may talk to a store;
this answers *whether we may talk to it at all right now*.

Why it is shared rather than scheduler-private: the breaker used to live inside
`PriceCheckScheduler` as a plain dict, so only background checks stood down after a bot wall.
Every interactive path — quick-add preview, the first check after a confirm, the "Kolla nu"
button — kept fetching a store that had just challenged us, and those are precisely the
requests a human repeats when the page says "kunde inte hämta sidan". Against a WAF that flags
IPs (ICA's AWS WAF), retrying during a challenge is what extends the flag. One registry,
consulted by every caller, means a block against a store stops ALL traffic to that store.

Escalation: a flat cooldown re-probes at the same cadence forever. Each consecutive block
doubles the wait (base → 2× → 4× …, capped), and the first success resets the count, so a
store that is merely having a moment recovers fast while a store that has genuinely flagged us
is left alone for hours.

In-memory and single-process, like the add-pause: the app is one uvicorn process, and a
restart at worst re-probes once. The scheduler additionally pushes `next_check_at` out in the
DB, so the background side of the cooldown survives a restart anyway.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# First cooldown after a block. Read from the scheduler's original env name so existing
# deployments keep their tuning; it is no longer scheduler-specific.
BASE_COOLDOWN_MINUTES = float(os.getenv("SCHEDULER_STORE_BLOCK_COOLDOWN_MINUTES", "30"))
# Ceiling for the doubling. An AWS WAF IP flag can persist for hours; probing a walled store
# every 30 min for a day is both useless and the thing that keeps the flag alive.
MAX_COOLDOWN_MINUTES = float(os.getenv("STORE_BLOCK_MAX_COOLDOWN_MINUTES", "240"))


def _now() -> datetime:
    """Naive UTC — the whole codebase stores and compares naive UTC datetimes."""
    return datetime.now(UTC).replace(tzinfo=None)


class StoreBlockRegistry:
    """Per-store block state: when the cooldown lifts, and how many blocks in a row."""

    def __init__(
        self,
        base_minutes: float = BASE_COOLDOWN_MINUTES,
        max_minutes: float = MAX_COOLDOWN_MINUTES,
    ) -> None:
        self.base_minutes = base_minutes
        self.max_minutes = max_minutes
        # key -> instant the cooldown lifts.
        self._until: dict[object, datetime] = {}
        # key -> consecutive blocks, the exponent behind the doubling. Survives the cooldown
        # lapsing (that is the point: a store that blocks again right after a probe escalates)
        # and is cleared only by a success.
        self._strikes: dict[object, int] = {}
        # key -> human name, purely so the status endpoint and logs can say "ICA" not a UUID.
        self._names: dict[object, str] = {}

    def record_block(self, key: object, *, store_name: str = "", source: str = "") -> datetime:
        """Trip the breaker for ``key`` and return the instant it lifts.

        ``source`` names the caller (scheduler / manual-check / quick-add) so the log answers
        "who found the wall", which is the first question when reading back a blocked evening.
        """
        strikes = self._strikes.get(key, 0) + 1
        self._strikes[key] = strikes
        minutes = min(self.base_minutes * (2 ** (strikes - 1)), self.max_minutes)
        until = _now() + timedelta(minutes=minutes)
        self._until[key] = until
        if store_name:
            self._names[key] = store_name
        logger.warning(
            "BLOCKED by %s (via %s) — consecutive block #%d, standing down for %.0f min "
            "(until %s); all requests to this store are held",
            store_name or key,
            source or "unknown",
            strikes,
            minutes,
            until,
        )
        return until

    def record_success(self, key: object, *, store_name: str = "") -> None:
        """A request to ``key`` came back as a real page — clear the breaker and the escalation.

        Called on every successful check, not just after a block, so the strike count can never
        drift upward across days of healthy traffic.
        """
        if key not in self._strikes and key not in self._until:
            return
        strikes = self._strikes.pop(key, 0)
        self._until.pop(key, None)
        logger.info(
            "Store %s answered normally again — block cleared after %d consecutive block(s)",
            store_name or self._names.get(key) or key,
            strikes,
        )

    def blocked_until(self, key: object) -> datetime | None:
        """The instant ``key``'s cooldown lifts, or None if it may be requested now.

        Lapsed cooldowns are cleared here (lazily) but the strike count is kept: the probe that
        follows a lapse is on trial, and a second wall must escalate rather than restart at base.
        """
        until = self._until.get(key)
        if until is None:
            return None
        if _now() >= until:
            del self._until[key]
            logger.info(
                "Cooldown for store %s elapsed — allowing one probe (%d prior block(s))",
                self._names.get(key) or key,
                self._strikes.get(key, 0),
            )
            return None
        return until

    def is_blocked(self, key: object) -> bool:
        return self.blocked_until(key) is not None

    def snapshot(self) -> list[dict[str, str | int]]:
        """Currently-blocked stores, for /scheduler/status and the portal status box."""
        out: list[dict[str, str | int]] = []
        for key in list(self._until):
            until = self.blocked_until(key)
            if until is None:
                continue
            out.append(
                {
                    "store": str(self._names.get(key) or key),
                    "blocked_until": until.isoformat(),
                    "consecutive_blocks": self._strikes.get(key, 0),
                }
            )
        return sorted(out, key=lambda row: str(row["store"]))
