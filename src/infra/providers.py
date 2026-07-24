from infra.email import ResendEmailService
from infra.fetcher import WebFetcher
from infra.rate_limiter import StoreRateLimiter

_fetcher_instance: WebFetcher | None = None
_email_service_instance: ResendEmailService | None = None
_rate_limiter_instance: StoreRateLimiter | None = None


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


def get_email_service() -> ResendEmailService:
    global _email_service_instance
    if _email_service_instance is None:
        _email_service_instance = ResendEmailService()
    return _email_service_instance
