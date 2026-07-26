"""httpx async fetcher implementing IFetcher."""

import asyncio
import logging
from html.parser import HTMLParser
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# A bot wall answered, not the page. ICA sits behind CloudFront + AWS WAF: 202 is a WAF JS
# CHALLENGE (its body is an awsWafCookieDomainList/gokuProps proof-of-work that needs a real
# browser to mint an `aws-waf-token` cookie), 403 is CloudFront's "Request blocked", 429 an
# explicit rate-limit. These FAIL FAST — retrying within a few seconds cannot solve a JS
# challenge and only adds load to an already-flagged IP — and are reported blocked=True so the
# scheduler can cool the whole store down (a per-store circuit breaker) instead of working
# through its other due links. (Treating a 202 as a *successful* 2xx is what raise_for_status
# once did, silently feeding an empty page to the extractors — that trap stays fixed.)
_WAF_BLOCK_STATUSES = frozenset({202, 403, 429})

# Transient origin failures — worth a short bounded retry, unlike a WAF wall.
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})

# Short, bounded backoff for the transient path. Bounded on purpose: an interactive quick-add
# caller is waiting, and an origin blip usually clears within seconds. Worst added latency ≈
# sum of these delays; after the last one we give up and report the failure.
_RETRY_DELAYS_SECONDS = (1.5, 4.0)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip <= 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


class WebFetcher:
    """Simple async web fetcher with browser headers and HTML text extraction."""

    def __init__(self) -> None:
        # Headers of a real top-level Chrome navigation. A WAF/CDN's bot-detection
        # fingerprints requests, and a "Chrome" User-Agent arriving WITHOUT the client
        # hints (Sec-Ch-Ua) and Sec-Fetch-* headers a real Chrome always sends is an
        # inconsistency that reads as automated — which is what invites the JS challenge
        # (the empty HTTP 202 we saw from ICA's CloudFront). Keeping the UA version and
        # the Sec-Ch-Ua brand list in step, plus the navigation Sec-Fetch-* set, makes us
        # look like a browser. This does NOT beat a genuine per-IP rate limit (that is the
        # politeness ledger's job) — it just lowers how often we trip the bot challenge.
        # Bump CHROME_MAJOR periodically; a stale major version is itself a mild bot tell.
        chrome_major = "143"
        client_kwargs: dict[str, Any] = dict(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{chrome_major}.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"
                ),
                "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
                "sec-ch-ua": (
                    f'"Chromium";v="{chrome_major}", '
                    f'"Google Chrome";v="{chrome_major}", '
                    '"Not(A:Brand";v="24"'
                ),
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
            },
        )

        # HTTP/2, because the headers above are only half a fingerprint. Real Chrome ALWAYS
        # negotiates h2 with a modern CDN, so a client that claims to be Chrome and then speaks
        # HTTP/1.1 contradicts itself at the connection layer — before a single header is read.
        # This does NOT reproduce Chrome's exact h2 SETTINGS/priority fingerprint (only
        # curl_cffi or a real browser does, and those remain the un-built next levers); it
        # removes one free tell. `h2` is a declared dependency via httpx[http2]; the fallback
        # exists so a broken install degrades to HTTP/1.1 instead of a fetcher that cannot be
        # constructed at all — a no-price app is worse than a slightly more detectable one.
        try:
            self._client = httpx.AsyncClient(http2=True, **client_kwargs)
        except ImportError:
            logger.warning(
                "h2 is not installed — falling back to HTTP/1.1, which is a bot tell "
                "for a client sending Chrome headers. Install httpx[http2]."
            )
            self._client = httpx.AsyncClient(**client_kwargs)

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch a page, returning {ok, text, html, error, blocked}.

        ok is True ONLY for a real page: a 2xx with a non-empty body. A WAF wall
        (_WAF_BLOCK_STATUSES) fails FAST with blocked=True — no retry, since retrying can't
        clear a JS challenge. A transient failure (5xx, network, empty body) is retried a few
        times, then reported ok=False. Either way callers surface "blocked/failed, try again"
        instead of extracting from an empty page.
        """
        last_error = "unknown error"
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            transient = False
            try:
                response = await self._client.get(url)
            except Exception as e:  # network error / timeout — transient, worth a retry
                last_error = str(e)
                transient = True
            else:
                status = response.status_code
                body = response.text
                if status in _WAF_BLOCK_STATUSES:
                    # Bot wall — fail fast and tell the caller it was a block (not a dead page),
                    # so the scheduler cools the whole store down instead of poking it further.
                    # Log it HERE, at the point of detection: this is the only place that knows
                    # the status code and the body size, and a WAF challenge used to produce no
                    # log line at all — the first evidence was a caller's generic "fetch failed".
                    logger.warning(
                        "Bot wall from %s — HTTP %d, %d-byte body; failing fast (no retry) "
                        "and reporting blocked",
                        url,
                        status,
                        len(body),
                    )
                    return {
                        "url": url,
                        "ok": False,
                        "text": "",
                        "html": "",
                        "error": f"blocked (HTTP {status})",
                        "blocked": True,
                    }
                elif status in _TRANSIENT_STATUSES or not body.strip():
                    # A transient origin error or an empty body — retry, do not trust it.
                    last_error = f"transient or empty response (HTTP {status}, {len(body)} bytes)"
                    transient = True
                elif status >= 400:
                    # A hard client error (404, 410, …) — a real answer, not a wall;
                    # retrying will not change it, so fail immediately.
                    logger.warning("Fetch of %s failed with HTTP %d — not retrying", url, status)
                    return {
                        "url": url,
                        "ok": False,
                        "text": "",
                        "html": "",
                        "error": f"HTTP {status}",
                    }
                else:
                    text = _extract_text(body)
                    # "html" carries the raw page for structured extraction (JSON-LD
                    # lives in <script> tags, which _extract_text strips out).
                    return {"url": url, "ok": True, "text": text, "html": body, "error": None}

            if transient and attempt < len(_RETRY_DELAYS_SECONDS):
                delay = _RETRY_DELAYS_SECONDS[attempt]
                logger.info(
                    "Fetch of %s failed (%s); retry %d/%d in %.1fs",
                    url,
                    last_error,
                    attempt + 1,
                    len(_RETRY_DELAYS_SECONDS),
                    delay,
                )
                await asyncio.sleep(delay)

        logger.warning(
            "Fetch of %s gave up after %d attempts: %s",
            url,
            len(_RETRY_DELAYS_SECONDS) + 1,
            last_error,
        )
        return {"url": url, "ok": False, "text": "", "html": "", "error": last_error}

    async def fetch_json(self, url: str) -> dict[str, Any]:
        """GET a store's JSON API endpoint, returning {ok, data, error, blocked}.

        Goes through the SAME client as page fetches on purpose: same Chrome fingerprint,
        same h2 connection, same TLS session — the way a real SPA's XHR rides the connection
        its page opened. The per-request headers below repaint the navigation headers as an
        XHR (Accept: application/json, sec-fetch-mode: cors); a bare per-call httpx client
        here used to announce `python-httpx` over HTTP/1.1 to the very host the page fetch
        had just spoken Chrome-h2 to.

        No retries: the only caller sits inside the extraction ladder with an LLM fallback
        behind it, and a WAF answer (blocked=True) must fail fast for the same reason
        fetch() does — see _WAF_BLOCK_STATUSES.
        """
        try:
            response = await self._client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                },
            )
        except Exception as e:  # network error / timeout
            logger.warning("JSON fetch of %s failed: %s", url, e)
            return {"url": url, "ok": False, "data": None, "error": str(e), "blocked": False}

        status = response.status_code
        if status in _WAF_BLOCK_STATUSES:
            # Same point-of-detection logging contract as fetch(): only this frame knows the
            # status and body size, and an unlogged wall is how blocks stay invisible in prod.
            logger.warning(
                "Bot wall from %s — HTTP %d, %d-byte body; failing fast (no retry) "
                "and reporting blocked",
                url,
                status,
                len(response.content),
            )
            return {
                "url": url,
                "ok": False,
                "data": None,
                "error": f"blocked (HTTP {status})",
                "blocked": True,
            }
        if status != 200:
            return {
                "url": url,
                "ok": False,
                "data": None,
                "error": f"HTTP {status}",
                "blocked": False,
            }
        try:
            data = response.json()
        except Exception as e:
            logger.warning("Non-JSON body from %s: %s", url, e)
            return {"url": url, "ok": False, "data": None, "error": str(e), "blocked": False}
        return {"url": url, "ok": True, "data": data, "error": None, "blocked": False}

    async def close(self) -> None:
        await self._client.aclose()


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.get_text()
    # Truncate very long pages to keep LLM prompt size reasonable
    if len(text) > 12000:
        text = text[:12000] + "\n...\n"
    return text
