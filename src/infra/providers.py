from infra.check_log import CheckAttemptLog
from infra.db import async_session_factory
from infra.email import ResendEmailService
from infra.fetcher import WebFetcher
from infra.ica_leaflet import IcaLeafletCache
from infra.rate_limiter import StoreRateLimiter
from infra.store_block import StoreBlockRegistry

_fetcher_instance: WebFetcher | None = None
_email_service_instance: ResendEmailService | None = None
_rate_limiter_instance: StoreRateLimiter | None = None
_block_registry_instance: StoreBlockRegistry | None = None
_check_log_instance: CheckAttemptLog | None = None
_leaflet_instance: IcaLeafletCache | None = None


def get_fetcher() -> WebFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = WebFetcher()
    return _fetcher_instance


def get_rate_limiter() -> StoreRateLimiter:
    """The process-wide politeness ledger shared by the scheduler and quick-add.

    A single instance so a store's throttle budget spans background checks AND
    interactive fetches — the two used to be blind to each other (see StoreRateLimiter).
    """
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = StoreRateLimiter()
    return _rate_limiter_instance


def get_block_registry() -> StoreBlockRegistry:
    """The process-wide circuit breaker shared by the scheduler and every interactive path.

    A single instance so a bot wall found by ANY caller silences the store for ALL of them.
    It lived inside the scheduler until v0.28.0, which meant a human mashing "Kolla nu" kept
    hitting a store that was actively challenging us (see StoreBlockRegistry).
    """
    global _block_registry_instance
    if _block_registry_instance is None:
        _block_registry_instance = StoreBlockRegistry()
    return _block_registry_instance


def get_check_log() -> CheckAttemptLog:
    """THE durable record of outgoing checks, shared by the scheduler and every interactive path.

    Its own session factory, not the request's session: the endpoints raise on a failed or
    blocked check before they commit, and a row in that transaction dies with it — losing
    exactly the attempts worth recording.
    """
    global _check_log_instance
    if _check_log_instance is None:
        _check_log_instance = CheckAttemptLog(async_session_factory)
    return _check_log_instance


def get_leaflet() -> IcaLeafletCache:
    """THE veckoblad reader, shared so a butik's leaflet is fetched once per local day.

    A single instance across the scheduler and the interactive "Kolla nu" path for the same
    reason the ledger and the breaker are shared: 44 due ICA links in one cycle must cost
    ONE request to www.ica.se, not 44. It rides the same ledger and breaker, keyed on that
    host rather than on a store id — www.ica.se is not the host the products come from.
    """
    global _leaflet_instance
    if _leaflet_instance is None:
        _leaflet_instance = IcaLeafletCache(get_fetcher(), get_rate_limiter(), get_block_registry())
    return _leaflet_instance


def get_email_service() -> ResendEmailService:
    global _email_service_instance
    if _email_service_instance is None:
        _email_service_instance = ResendEmailService()
    return _email_service_instance
