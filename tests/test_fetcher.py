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


class TestBrowserHeaders:
    def test_sends_a_consistent_browser_fingerprint(self) -> None:
        """A Chrome UA WITH the matching client hints + Sec-Fetch-* — inconsistency here
        is what invites a WAF's bot challenge (the empty 202 from ICA)."""
        headers = WebFetcher()._client.headers  # httpx lowercases keys

        ua = headers["user-agent"]
        assert "Chrome/" in ua
        # UA major version and the Sec-Ch-Ua brand version agree — the tell a WAF checks.
        major = ua.split("Chrome/")[1].split(".")[0]
        assert f'"Google Chrome";v="{major}"' in headers["sec-ch-ua"]
        for h in (
            "sec-fetch-mode",
            "sec-fetch-dest",
            "sec-fetch-site",
            "upgrade-insecure-requests",
        ):
            assert h in headers


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
    async def test_202_waf_challenge_fails_fast_and_is_flagged_blocked(self) -> None:
        """The exact ICA shape: a 202 AWS WAF challenge. No retry (retrying can't solve a JS
        challenge), and blocked=True so the scheduler can cool the whole store down."""
        fetcher = _make_fetcher([_response(202, ""), _response(202, ""), _response(202, "")])

        result = await fetcher.fetch("https://handlaprivatkund.ica.se/p")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["html"] == ""
        assert "HTTP 202" in result["error"]
        assert fetcher._client.get.await_count == 1  # failed fast, no retry burst at the WAF

    async def test_403_request_blocked_fails_fast(self) -> None:
        fetcher = _make_fetcher([_response(403, "Request blocked")] * 3)

        result = await fetcher.fetch("https://handlaprivatkund.ica.se/p")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert "HTTP 403" in result["error"]
        assert fetcher._client.get.await_count == 1

    async def test_429_rate_limit_fails_fast(self) -> None:
        fetcher = _make_fetcher([_response(429, "slow down")] * 3)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert fetcher._client.get.await_count == 1

    async def test_405_is_a_wall_not_a_method_error(self) -> None:
        """ICA answers 405 to a plain GET when it is walling us (prod, 2026-07-27).

        It is not a real "method not allowed": we only ever GET, these are pages a browser
        opens, and the SAME urls returned 200, 405 and 202 within one morning. The 405s
        arrive in runs beginning on the first probe after a cooldown lapses. Classified as a
        hard 4xx it left blocked=False, so the breaker never re-tripped and the scheduler
        kept fetching ICA at its 60-90 s cadence right through the wall.
        """
        fetcher = _make_fetcher([_response(405, "")] * 3)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert "HTTP 405" in result["error"]
        # Fails fast like every other wall — a 405 is not going to change in 1.5 seconds.
        assert fetcher._client.get.await_count == 1

    async def test_404_is_still_a_hard_error_not_a_wall(self) -> None:
        """The counterpart: a genuinely missing page must NOT cool the whole store down."""
        fetcher = _make_fetcher([_response(404, "not found")] * 3)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert result.get("blocked") is not True
        assert fetcher._client.get.await_count == 1

    async def test_200_but_empty_body_is_a_transient_failure(self) -> None:
        """Empty body carries no status tell, so it is transient (retried), not a WAF block."""
        fetcher = _make_fetcher([_response(200, "   ")] * 3)

        result = await fetcher.fetch("https://example.test/p")

        assert result["ok"] is False
        assert not result.get("blocked")
        assert "0 bytes" in result["error"] or "3 bytes" in result["error"]


class TestRetry:
    async def test_transient_5xx_then_success_recovers(self, _no_sleep) -> None:
        """A 503 (transient origin blip) that clears on the next attempt yields a real page."""
        html = "<html><body>Falukorv</body></html>"
        fetcher = _make_fetcher([_response(503, ""), _response(200, html)])

        result = await fetcher.fetch("https://example.test/p")

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
        """Three total attempts (initial + two backoffs) on a transient failure, then gives up."""
        fetcher = _make_fetcher([_response(503, "origin down")] * 5)

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


class TestFetchJson:
    """fetch_json shares fetch()'s client and its WAF contract, minus the retries."""

    def _json_response(self, status_code: int, data: object = None) -> SimpleNamespace:
        resp = SimpleNamespace(status_code=status_code, content=b"{}")
        if isinstance(data, Exception):

            def _raise() -> object:
                raise data

            resp.json = _raise
        else:
            resp.json = lambda: data
        return resp

    async def test_ok_returns_parsed_data(self) -> None:
        fetcher = _make_fetcher([self._json_response(200, {"priceValue": 29.9})])

        result = await fetcher.fetch_json("https://example.test/api/p/1")

        assert result["ok"] is True
        assert result["data"] == {"priceValue": 29.9}
        assert result["blocked"] is False

    async def test_uses_the_shared_client_with_xhr_headers(self) -> None:
        """One client for pages AND the API: the whole point is that the REST call rides the
        same Chrome-fingerprinted h2 connection, repainted as an XHR (accept: json, cors)."""
        fetcher = _make_fetcher([self._json_response(200, {})])

        await fetcher.fetch_json("https://example.test/api/p/1")

        headers = fetcher._client.get.await_args.kwargs["headers"]
        assert headers["Accept"] == "application/json"
        assert headers["sec-fetch-mode"] == "cors"
        assert headers["sec-fetch-site"] == "same-origin"

    async def test_waf_wall_is_blocked_true_and_not_retried(self) -> None:
        fetcher = _make_fetcher([self._json_response(403)] * 3)

        result = await fetcher.fetch_json("https://example.test/api/p/1")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert fetcher._client.get.await_count == 1  # fail fast — no retry burst at the WAF

    async def test_http_error_is_ok_false_not_blocked(self) -> None:
        fetcher = _make_fetcher([self._json_response(404)])

        result = await fetcher.fetch_json("https://example.test/api/p/1")

        assert result["ok"] is False
        assert result["blocked"] is False
        assert "HTTP 404" in result["error"]

    async def test_non_json_body_is_ok_false(self) -> None:
        fetcher = _make_fetcher([self._json_response(200, ValueError("not json"))])

        result = await fetcher.fetch_json("https://example.test/api/p/1")

        assert result["ok"] is False
        assert result["blocked"] is False

    async def test_network_error_is_ok_false_not_blocked(self) -> None:
        fetcher = _make_fetcher([TimeoutError("timed out")])

        result = await fetcher.fetch_json("https://example.test/api/p/1")

        assert result["ok"] is False
        assert result["blocked"] is False
        assert "timed out" in result["error"]


class TestNegotiatedProtocolLogging:
    """One line per host per process settles "did we actually speak h2 to ICA today" —
    the h2 half of the fingerprint was verified manually but had no prod evidence at all."""

    def _versioned(self, status_code: int, text: str, http_version: str) -> SimpleNamespace:
        return SimpleNamespace(status_code=status_code, text=text, http_version=http_version)

    async def test_h2_is_logged_once_per_host(self, caplog) -> None:
        import logging

        fetcher = _make_fetcher(
            [
                self._versioned(200, "<html>a</html>", "HTTP/2"),
                self._versioned(200, "<html>b</html>", "HTTP/2"),
            ]
        )

        with caplog.at_level(logging.INFO, logger="infra.fetcher"):
            await fetcher.fetch("https://example.test/p1")
            await fetcher.fetch("https://example.test/p2")

        lines = [r for r in caplog.records if "Negotiated" in r.message]
        assert len(lines) == 1  # once per HOST, not per request
        assert "HTTP/2" in lines[0].getMessage()
        assert "example.test" in lines[0].getMessage()

    async def test_a_1_1_downgrade_is_a_warning(self, caplog) -> None:
        """HTTP/1.1 under Chrome headers IS the fingerprint contradiction http2 was added
        to remove — it must stand out in the log, not blend in as routine INFO."""
        import logging

        fetcher = _make_fetcher([self._versioned(200, "<html>a</html>", "HTTP/1.1")])

        with caplog.at_level(logging.INFO, logger="infra.fetcher"):
            await fetcher.fetch("https://example.test/p1")

        [line] = [r for r in caplog.records if "Negotiated" in r.message]
        assert line.levelno == logging.WARNING

    async def test_fetch_json_also_logs_the_protocol(self, caplog) -> None:
        import logging

        resp = SimpleNamespace(status_code=200, content=b"{}", http_version="HTTP/2")
        resp.json = lambda: {}
        fetcher = _make_fetcher([resp])

        with caplog.at_level(logging.INFO, logger="infra.fetcher"):
            await fetcher.fetch_json("https://api.example.test/p/1")

        assert any("Negotiated HTTP/2" in r.getMessage() for r in caplog.records)

    async def test_a_wall_response_still_reveals_its_protocol(self, caplog) -> None:
        """A 202 challenge is an answer too — whether the WAF spoke h2 to us is evidence."""
        import logging

        fetcher = _make_fetcher([self._versioned(202, "", "HTTP/2")])

        with caplog.at_level(logging.INFO, logger="infra.fetcher"):
            result = await fetcher.fetch("https://example.test/p1")

        assert result["blocked"] is True
        assert any("Negotiated HTTP/2" in r.getMessage() for r in caplog.records)
