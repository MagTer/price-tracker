"""Fetches a butik's veckoblad, politely, and caches it for the local day.

The parse and the join live in ``domain/ica_leaflet.py``; this is the half that talks to
the network, which is why it — and not the extractor — owns the ledger slot and the
breaker. A store-HTML extractor makes no HTTP calls by rule, so the cross-check could
never have lived inside IcaExtractor.

Two things it must get right, both of which fail silently if broken:

* **Every failure answers None (unknown), never an empty leaflet.** A wall, a timeout, a
  changed page shape and a butik with no configured URL all mean "we did not read the
  veckoblad", and the caller records no verdict. Answering "{}" would stamp every ICA
  campaign that day as absent from the leaflet — a confident wrong answer written into
  history, exactly the failure the app's degrade-to-unknown rule exists to prevent.
* **It never raises into a price check.** The check is the thing; this is a note on it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from domain.ica_leaflet import LeafletOffer, leaflet_url_for, parse_leaflet
from domain.protocols import IBlockRegistry, IFetcher, IRateLimiter
from domain.schedule import STORE_TIMEZONE

logger = logging.getLogger(__name__)

# www.ica.se is a DIFFERENT host from handlaprivatkund.ica.se, so it gets its own ledger
# key and its own breaker key: a wall on the leaflet page is no evidence about the
# e-handel host, and must not stop 44 product checks. The reverse holds too.
_LEDGER_KEY = "host:www.ica.se"
_BREAKER_KEY = "host:www.ica.se"
_MIN_INTERVAL_SECONDS = 5.0
_MAX_WAIT_SECONDS = 15.0

# A leaflet runs for a week and is fetched at most once per butik per local day. A failed
# read is remembered only briefly, so one bad minute cannot blind the rest of the day —
# but a cycle of 44 due ICA links still costs at most one retry per window.
_FAILURE_TTL = timedelta(minutes=15)


class IcaLeafletCache:
    """THE reader of ICA butiksblad — one fetch per butik per local day, shared."""

    def __init__(
        self,
        fetcher: IFetcher,
        rate_limiter: IRateLimiter,
        block_registry: IBlockRegistry,
    ) -> None:
        self._fetcher = fetcher
        self._rate_limiter = rate_limiter
        self._blocks = block_registry
        self._cache: dict[str, tuple[datetime, dict[str, LeafletOffer] | None]] = {}
        self._lock = asyncio.Lock()

    async def offers_for(self, store_url: str) -> dict[str, LeafletOffer] | None:
        """The butik's advertised offers, or None when we do not know."""
        url = leaflet_url_for(store_url)
        if url is None:
            return None

        # One lock for the whole cache rather than one per URL: the scheduler checks links
        # sequentially and there are two butiker, so contention is theoretical — while a
        # double fetch would spend a slot on a store we are trying to be polite to.
        async with self._lock:
            now = datetime.now(tz=STORE_TIMEZONE)
            cached = self._cache.get(url)
            if cached is not None and now < cached[0]:
                return cached[1]

            offers = await self._read(url)
            expires = _next_local_midnight(now) if offers is not None else now + _FAILURE_TTL
            self._cache[url] = (expires, offers)
            return offers

    async def _read(self, url: str) -> dict[str, LeafletOffer] | None:
        """One fetch + parse. Answers None on every failure, and never raises."""
        # blocked_until, not is_blocked: the protocol domain/ depends on carries the
        # former, and the two answer the same question.
        if self._blocks.blocked_until(_BREAKER_KEY) is not None:
            logger.info("ICA leaflet: %s is cooling down, skipping the veckoblad", _BREAKER_KEY)
            return None
        try:
            await self._rate_limiter.acquire(
                _LEDGER_KEY, _MIN_INTERVAL_SECONDS, max_wait=_MAX_WAIT_SECONDS
            )
            result = await self._fetcher.fetch(url)
        except Exception:
            # A note on a price check may never take the check down with it.
            logger.exception("ICA leaflet: fetching %s raised", url)
            return None

        if result.get("blocked"):
            self._blocks.record_block(
                _BREAKER_KEY, store_name="ICA erbjudandesida", source="leaflet"
            )
            logger.warning("ICA leaflet: bot wall on %s - %s", url, result.get("error"))
            return None
        if not result.get("ok") or not result.get("html"):
            logger.warning("ICA leaflet: fetch of %s failed - %s", url, result.get("error"))
            return None

        # The page answered, so the host is talking to us — the same "reached the store"
        # predicate every other caller resets strikes on, even if the parse then fails.
        self._blocks.record_success(_BREAKER_KEY, store_name="ICA erbjudandesida")

        offers = parse_leaflet(str(result.get("html")))
        if offers is None:
            logger.warning("ICA leaflet: %s carried no readable weeklyOffers", url)
            return None
        logger.info("ICA leaflet: %s advertises %d offers", url, len(offers))
        return offers


def _next_local_midnight(now: datetime) -> datetime:
    """Start of the next Swedish civil day — a leaflet answer is good for today only."""
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
