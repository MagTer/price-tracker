"""httpx async fetcher implementing IFetcher."""

from html.parser import HTMLParser
from typing import Any

import httpx


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
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"
                ),
                "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

    async def fetch(self, url: str) -> dict[str, Any]:
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            text = _extract_text(response.text)
            # "html" carries the raw page for structured extraction (JSON-LD
            # lives in <script> tags, which _extract_text strips out).
            return {"url": url, "ok": True, "text": text, "html": response.text, "error": None}
        except Exception as e:
            return {"url": url, "ok": False, "text": "", "html": "", "error": str(e)}

    async def search(self, query: str, k: int = 5, lang: str = "en") -> dict[str, Any]:
        return {"query": query, "results": []}

    async def research(
        self, query: str, k: int = 5, model: str = "gpt-3.5-turbo"
    ) -> dict[str, Any]:
        return {"query": query, "sources": [], "summary": ""}

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
