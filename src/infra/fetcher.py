"""Async fetcher implementing IFetcher, over curl_cffi (default) or httpx.

The transport is swappable ON PURPOSE, via env `FETCHER_TRANSPORT`. curl_cffi impersonates
a real Chrome down at the TLS and HTTP/2 layers, which is the one part of the fingerprint
headers cannot fix — but whether that actually changes how often a store walls us is an
open question this app is in a position to MEASURE rather than assume. One env var and a
restart flips it back, and every fetch logs which transport answered, so a week of prod
logs settles it. See the transport bullet in CLAUDE.md.
"""

import asyncio
import logging
import os
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

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
#
# 405 joined the set on 2026-07-27, from prod evidence. It is NOT a "this method is not allowed
# on this resource" answer: we only ever GET, and these are pages a browser opens. The same
# URLs returned all three of 200, 405 and 202 within one morning — Ketchup 1,25 kg priced at
# 06:42 and 405 at 10:01; Tortilla priced at 09:42 and 405 at 10:03; Kaviar 405 at 07:56 and a
# 202 challenge at 10:13. And the 405s arrive in RUNS that start on the first probe after a
# cooldown lapses (07:45 block → 07:50, 07:55, 07:56, 07:57, 08:02 all 405). Classified as a
# hard 4xx it did not set blocked, so the breaker never re-tripped and the scheduler kept
# feeding ICA at its 60-90 s cadence straight through the wall — exactly the hammering the
# breaker exists to prevent — and each 405 also pushed its link +24h out of the förmiddag
# window. Cost of being wrong about this: a genuinely 405-ing URL gets cooled down and retried
# instead of failing fast. Cost of being wrong the other way is what prod just showed.
_WAF_BLOCK_STATUSES = frozenset({202, 403, 405, 429})

# Transient origin failures — worth a short bounded retry, unlike a WAF wall.
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})

# Headers a real Chrome sends ONLY on a top-level navigation, never on an XHR. Both
# transports carry them on every request by default (the impersonation profile paints them
# in under curl_cffi, the session headers under httpx), so fetch_json must actively remove
# them — an "XHR" arriving with upgrade-insecure-requests is its own small fingerprint tell.
_NAVIGATION_ONLY_HEADERS = ("sec-fetch-user", "upgrade-insecure-requests")

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


# The Chrome version the httpx path claims. curl_cffi does NOT read this — `impersonate`
# ships its own matched UA, and hand-writing one on top would re-create exactly the
# self-contradiction the impersonation exists to remove. Bump it when the fallback is stale.
_CHROME_MAJOR = "143"

# curl_cffi impersonation target. "chrome" is the library's alias for its newest Chrome
# profile, so a curl_cffi upgrade also refreshes the fingerprint — the opposite of the
# hand-maintained _CHROME_MAJOR above, which goes stale silently.
_IMPERSONATE = "chrome"

# curl's CURLINFO_HTTP_VERSION is an int enum, httpx's http_version a string. Normalising
# here keeps _log_negotiated_protocol (and prod's logs) transport-independent. NOTE 3 is
# HTTP/2, not HTTP/3 — HTTP/3 is 30. Getting that backwards is how a session concluded
# curl_cffi bought us a protocol upgrade it did not.
_CURL_HTTP_VERSIONS = {
    1: "HTTP/1.0",
    2: "HTTP/1.1",
    3: "HTTP/2",
    4: "HTTP/2",
    5: "HTTP/2",
    30: "HTTP/3",
    31: "HTTP/3",
}


class WebFetcher:
    """Async web fetcher with a browser fingerprint and HTML text extraction.

    `transport` records which client actually got built ("curl_cffi" or "httpx") — read it
    in tests, and it is logged once at construction so prod can attribute a week of block
    rates to a transport rather than to a hunch.
    """

    def __init__(self, transport: str | None = None) -> None:
        requested = (transport or os.getenv("FETCHER_TRANSPORT") or "curl_cffi").strip().lower()
        if requested not in ("curl_cffi", "httpx"):
            # Loud, because a typo would silently drop the impersonation and the whole point
            # of the A/B is knowing which transport a week of logs belongs to.
            logger.warning(
                "FETCHER_TRANSPORT=%r is not one of curl_cffi/httpx — using curl_cffi", requested
            )
            requested = "curl_cffi"

        self.transport = "httpx"
        if requested == "curl_cffi":
            client = self._build_curl_client()
            if client is not None:
                self._client = client
                self.transport = "curl_cffi"
        if self.transport == "httpx":
            self._client = self._build_httpx_client()
        logger.info("WebFetcher using the %s transport (requested %s)", self.transport, requested)

        # Hosts whose negotiated protocol has been logged. h2 was verified manually against
        # the stores, but prod evidence must live in prod logs: one line per host per process
        # settles "did we actually speak h2 to ICA today" without a line per request.
        self._protocol_logged_hosts: set[str] = set()

    def _build_curl_client(self) -> Any | None:
        """A curl_cffi session impersonating Chrome, or None if it cannot be built.

        This is the whole point of the transport: headers are only half a fingerprint, and
        the half they cannot reach is the TLS ClientHello (cipher order, extensions, GREASE
        — the JA3) and the HTTP/2 SETTINGS/priority frames. A client sending Chrome's headers
        over an OpenSSL-default handshake contradicts itself before a byte of HTTP is read,
        and that contradiction is precisely what a bot-detection layer scores first.

        Returning None rather than raising mirrors the h2 fallback below: a native library
        that fails to load must degrade to a more detectable fetcher, never to no fetcher.
        """
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            logger.warning(
                "curl_cffi is not installed — falling back to httpx, which cannot impersonate "
                "Chrome's TLS or HTTP/2 fingerprint. Install curl_cffi to close that gap."
            )
            return None

        try:
            return AsyncSession(
                impersonate=_IMPERSONATE,
                timeout=15,
                allow_redirects=True,
                # Only the locale is ours. Everything else that identifies the "browser" —
                # User-Agent, sec-ch-ua, header ORDER — comes from the impersonation profile
                # and must stay consistent with the TLS handshake it ships with.
                headers={"Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7"},
            )
        except Exception as e:  # a broken native lib must not take the app down
            logger.warning("curl_cffi failed to initialise (%s) — falling back to httpx", e)
            return None

    def _build_httpx_client(self) -> httpx.AsyncClient:
        # Headers of a real top-level Chrome navigation. A WAF/CDN's bot-detection
        # fingerprints requests, and a "Chrome" User-Agent arriving WITHOUT the client
        # hints (Sec-Ch-Ua) and Sec-Fetch-* headers a real Chrome always sends is an
        # inconsistency that reads as automated — which is what invites the JS challenge
        # (the empty HTTP 202 we saw from ICA's CloudFront). Keeping the UA version and
        # the Sec-Ch-Ua brand list in step, plus the navigation Sec-Fetch-* set, makes us
        # look like a browser. This does NOT beat a genuine per-IP rate limit (that is the
        # politeness ledger's job) — it just lowers how often we trip the bot challenge.
        # Bump _CHROME_MAJOR periodically; a stale major version is itself a mild bot tell.
        chrome_major = _CHROME_MAJOR
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
        # This does NOT reproduce Chrome's exact h2 SETTINGS/priority fingerprint — that is
        # what the curl_cffi transport above is for; this only removes one free tell. `h2` is
        # a declared dependency via httpx[http2]; the fallback exists so a broken install
        # degrades to HTTP/1.1 instead of a fetcher that cannot be constructed at all — a
        # no-price app is worse than a slightly more detectable one.
        try:
            return httpx.AsyncClient(http2=True, **client_kwargs)
        except ImportError:
            logger.warning(
                "h2 is not installed — falling back to HTTP/1.1, which is a bot tell "
                "for a client sending Chrome headers. Install httpx[http2]."
            )
            return httpx.AsyncClient(**client_kwargs)

    def _log_negotiated_protocol(self, response: Any, url: str) -> None:
        """Log the negotiated HTTP version once per host — WARNING when it is not h2.

        HTTP/1.1 against a store is worth a WARNING, not an INFO: the client claims to be
        Chrome, and real Chrome always negotiates h2 with a modern CDN, so a 1.1 downgrade
        IS the fingerprint contradiction we added http2 to remove. Reads via getattr because
        tests stub responses minimally; a stub without http_version simply logs nothing.

        The transport is named in the line: comparing block rates between curl_cffi and
        httpx over a week only works if the log says which one answered.
        """
        host = urlsplit(url).netloc
        if not host or host in self._protocol_logged_hosts:
            return
        version = _normalise_http_version(getattr(response, "http_version", None))
        if not version:
            return
        self._protocol_logged_hosts.add(host)
        if version == "HTTP/2":
            logger.info("Negotiated %s with %s via %s", version, host, self.transport)
        else:
            logger.warning(
                "Negotiated %s with %s via %s — a client sending Chrome headers over %s "
                "contradicts its own fingerprint (real Chrome speaks h2 to a modern CDN)",
                version,
                host,
                self.transport,
                version,
            )

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
                self._log_negotiated_protocol(response, url)
                status = response.status_code
                body = response.text
                if status in _WAF_BLOCK_STATUSES:
                    # Bot wall — fail fast and tell the caller it was a block (not a dead page),
                    # so the scheduler cools the whole store down instead of poking it further.
                    # Log it HERE, at the point of detection: this is the only place that knows
                    # the status code and the body size, and a WAF challenge used to produce no
                    # log line at all — the first evidence was a caller's generic "fetch failed".
                    logger.warning(
                        "Bot wall from %s — HTTP %d, %d-byte body via %s; failing fast "
                        "(no retry) and reporting blocked",
                        url,
                        status,
                        len(body),
                        self.transport,
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

    async def fetch_json(self, url: str, referer: str | None = None) -> dict[str, Any]:
        """GET a store's JSON API endpoint, returning {ok, data, error, blocked}.

        Goes through the SAME client as page fetches on purpose: same Chrome fingerprint,
        same h2 connection, same TLS session — the way a real SPA's XHR rides the connection
        its page opened. The per-request headers below repaint the navigation headers as an
        XHR (Accept: application/json, sec-fetch-mode: cors); a bare per-call httpx client
        here used to announce `python-httpx` over HTTP/1.1 to the very host the page fetch
        had just spoken Chrome-h2 to.

        The repaint also REMOVES _NAVIGATION_ONLY_HEADERS, and removal is transport-specific
        because neither client documents the obvious spelling: an empty string value does
        NOT remove a header on either transport — both SEND it empty, echo-verified — so
        curl_cffi gets None (its explicit "disable this header" marker, serialised as
        "name:") and httpx gets the header deleted from a built request (its request-header
        merge has no removal semantics at all).

        `referer` is the page this XHR pretends to originate from. sec-fetch-site:
        same-origin with no Referer is a self-contradiction — a same-origin call by
        definition has a page behind it — so the one caller (the Willys API extractor)
        passes the product page URL it is checking.

        No retries: the only caller sits inside the extraction ladder with an LLM fallback
        behind it, and a WAF answer (blocked=True) must fail fast for the same reason
        fetch() does — see _WAF_BLOCK_STATUSES.
        """
        headers: dict[str, Any] = {
            "Accept": "application/json",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if referer:
            headers["Referer"] = referer
        try:
            if self.transport == "curl_cffi":
                for name in _NAVIGATION_ONLY_HEADERS:
                    headers[name] = None
                response = await self._client.get(url, headers=headers)
            else:
                request = self._client.build_request("GET", url, headers=headers)
                for name in _NAVIGATION_ONLY_HEADERS:
                    if name in request.headers:
                        del request.headers[name]
                response = await self._client.send(request)
        except Exception as e:  # network error / timeout
            logger.warning("JSON fetch of %s failed: %s", url, e)
            return {"url": url, "ok": False, "data": None, "error": str(e), "blocked": False}

        self._log_negotiated_protocol(response, url)
        status = response.status_code
        if status in _WAF_BLOCK_STATUSES:
            # Same point-of-detection logging contract as fetch(): only this frame knows the
            # status and body size, and an unlogged wall is how blocks stay invisible in prod.
            logger.warning(
                "Bot wall from %s — HTTP %d, %d-byte body via %s; failing fast "
                "(no retry) and reporting blocked",
                url,
                status,
                len(response.content),
                self.transport,
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
        # httpx says aclose(), curl_cffi says close() — and both are coroutines.
        closer = getattr(self._client, "aclose", None) or self._client.close
        await closer()


def _normalise_http_version(version: Any) -> str | None:
    """One string for a protocol two clients report differently.

    httpx gives "HTTP/2"; curl_cffi gives curl's CURLINFO_HTTP_VERSION int enum, where
    **3 means HTTP/2** and HTTP/3 is 30. Reading that 3 as "HTTP/3" is how a probe once
    concluded curl_cffi had bought a protocol upgrade it had not.
    """
    if version is None or version == "":
        return None
    if isinstance(version, str):
        return version
    try:
        return _CURL_HTTP_VERSIONS.get(int(version), f"HTTP-version {int(version)}")
    except (TypeError, ValueError):
        return None


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.get_text()
    # Truncate very long pages to keep LLM prompt size reasonable
    if len(text) > 12000:
        text = text[:12000] + "\n...\n"
    return text
