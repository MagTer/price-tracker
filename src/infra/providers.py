from infra.email import SmtpEmailService
from infra.fetcher import WebFetcher

_fetcher_instance: WebFetcher | None = None
_email_service_instance: SmtpEmailService | None = None


def get_fetcher() -> WebFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = WebFetcher()
    return _fetcher_instance


def get_email_service() -> SmtpEmailService:
    global _email_service_instance
    if _email_service_instance is None:
        _email_service_instance = SmtpEmailService()
    return _email_service_instance
