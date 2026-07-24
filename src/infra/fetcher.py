"""httpx async fetcher implementing IFetcher."""

import asyncio
import logging
from html.parser import HTMLParser
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Statuses that mean "a bot wall answered, not the page". A store's WAF/CDN (ICA sits
# behind CloudFront) returns these under load: 202 is a challenge/queue placeholder with
# an empty body, 403 is CloudFront's "Request blocked", 429 is an explicit rate-limit,
# and 5xx are transient origin failures. NONE of them carry product data, so treating
# them as a *successful* fetch — which is what raise_for_status() did for 202, since 202
# is a 2xx — silently fed an empty page to the JSON-LD/LLM extractors. That is precisely
# how a transient ICA block surfaced as "Product metadata extraction failed" instead of
# an honest "blocked, try again". These are retried a couple of times, then reported ok=False.
_RETRYABLE_BLOCK_STATUSES = frozenset({202, 403, 429, 500, 502, 503, 504})

# Short, bounded backoff. Bounded on purpose: an interactive quick-add caller is waiting
# on this, and a store's short rate-limit window usually reopens within a few seconds
# (verified against ICA — full pages returned again seconds after a block). Worst added
# latency ≈ sum of these delays; after the last one we give up and report the block.
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
        chrome_major = "133"
        self._client = httpx.AsyncClient(
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

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch a page, returning {ok, text, html, error}.

        ok is True ONLY for a real page: a 2xx with a non-empty body. A bot-wall
        response (see _RETRYABLE_BLOCK_STATUSES) or an empty body is a soft failure —
        retried a few times, then reported ok=False with a human-readable error — so
        callers surface "blocked, try again" instead of extracting from an empty page.
        """
        last_error = "unknown error"
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            blocked = False
            try:
                response = await self._client.get(url)
            except Exception as e:  # network error / timeout — transient, worth a retry
                last_error = str(e)
                blocked = True
            else:
                status = response.status_code
                body = response.text
                if status in _RETRYABLE_BLOCK_STATUSES or not body.strip():
                    # A challenge/rate-limit/empty response — retry, do not trust it.
                    last_error = f"blocked or empty response (HTTP {status}, {len(body)} bytes)"
                    blocked = True
                elif status >= 400:
                    # A hard client error (404, 410, …) — a real answer, not a wall;
                    # retrying will not change it, so fail immediately.
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

            if blocked and attempt < len(_RETRY_DELAYS_SECONDS):
                delay = _RETRY_DELAYS_SECONDS[attempt]
                logger.info(
                    "Fetch of %s blocked (%s); retry %d/%d in %.1fs",
                    url,
                    last_error,
                    attempt + 1,
                    len(_RETRY_DELAYS_SECONDS),
                    delay,
                )
                await asyncio.sleep(delay)

        return {"url": url, "ok": False, "text": "", "html": "", "error": last_error}

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
