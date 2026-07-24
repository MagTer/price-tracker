"""Tests for WebFetcher's block-detection and bounded retry (Fix A).

A store's WAF/CDN answers a burst with a bot wall, not the page: ICA's CloudFront
returns HTTP 202 with an empty challenge body, or 403 "Request blocked". The old
fetcher called raise_for_status(), which treats 202 as success, so an empty page
flowed on to the JSON-LD/LLM extractors and surfaced as "metadata extraction failed"
instead of an honest "blocked". These tests pin the corrected contract.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from infra.fetcher import WebFetcher


def _response(status_code: int, text: str) -> SimpleNamespace:
    """A stand-in for httpx.Response — fetch() only reads status_code and text."""
    return SimpleNamespace(status_code=status_code, text=text)


def _make_fetcher(responses: list) -> WebFetcher:
    """A WebFetcher whose HTTP client yields the given responses (or raises) in order."""
    fetcher = WebFetcher()
    fetcher._client.get = AsyncMock(side_effect=responses)
    return fetcher


# Patch sleep everywhere in this module so retry backoff does not actually wait.
@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("infra.fetcher.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        yield sleep_mock


class TestSuccess:
    async def test_real_page_returns_ok_with_html_and_text(self) -> None:
        html = "<html><body><p>Hej</p><script>ignored</script></body></html>"
        fetcher = _make_fetcher([_response(200, html)])

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is True
        assert result["html"] == html
        assert "Hej" in result["text"]
        assert result["error"] is None
        assert fetcher._client.get.await_count == 1


class TestBlockDetection:
    async def test_202_empty_body_is_a_soft_failure_not_success(self) -> None:
        """The exact ICA CloudFront challenge shape: 202 + empty body."""
        fetcher = _make_fetcher([_response(202, ""), _response(202, ""), _response(202, "")])

        result = await fetcher.fetch("https://handlaprivatkund.ica.se/p")

        assert result["ok"] is False
        assert result["html"] == ""
        assert "HTTP 202" in result["error"]

    async def test_403_request_blocked_is_a_soft_failure(self) -> None:
        fetcher = _make_fetcher([_response(403, "Request blocked")] * 3)

        result = await fetcher.fetch("https://handlaprivatkund.ica.se/p")

        assert result["ok"] is False
        assert "HTTP 403" in result["error"]

    async def test_200_but_empty_body_is_a_soft_failure(self) -> None:
        fetcher = _make_fetcher([_response(200, "   ")] * 3)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert "0 bytes" in result["error"] or "3 bytes" in result["error"]


class TestRetry:
    async def test_transient_block_then_success_recovers(self, _no_sleep) -> None:
        """A 202 that clears on the next attempt yields a real page — no error surfaced."""
        html = "<html><body>Falukorv</body></html>"
        fetcher = _make_fetcher([_response(202, ""), _response(200, html)])

        result = await fetcher.fetch("https://handlaprivatkund.ica.se/p")

        assert result["ok"] is True
        assert "Falukorv" in result["text"]
        assert fetcher._client.get.await_count == 2
        _no_sleep.assert_awaited_once()  # backed off exactly once, between the two attempts

    async def test_network_error_then_success_recovers(self) -> None:
        html = "<html><body>ok</body></html>"
        fetcher = _make_fetcher([TimeoutError("boom"), _response(200, html)])

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is True
        assert fetcher._client.get.await_count == 2

    async def test_gives_up_after_bounded_retries(self, _no_sleep) -> None:
        """Three total attempts (initial + two backoffs), then reports the block."""
        fetcher = _make_fetcher([_response(429, "slow down")] * 5)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert fetcher._client.get.await_count == 3
        assert _no_sleep.await_count == 2

    async def test_hard_client_error_is_not_retried(self) -> None:
        """A 404 is a real answer, not a wall — fail fast, do not burn retries on it."""
        fetcher = _make_fetcher([_response(404, "not found")])

        result = await fetcher.fetch("https://example.test/gone")

        assert result["ok"] is False
        assert "HTTP 404" in result["error"]
        assert fetcher._client.get.await_count == 1
