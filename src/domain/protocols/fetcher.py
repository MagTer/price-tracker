"""Protocol for web fetching and search services."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IFetcher(Protocol):
    """Abstract interface for web fetching and search operations.

    This protocol defines the contract for fetching web content,
    performing web searches, and conducting research queries.
    """

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch content from a URL.

        Args:
            url: The URL to fetch content from.

        Returns:
            Dictionary containing:
                - url: The fetched URL
                - ok: Boolean success indicator
                - text: Extracted text content
                - error: Error message if failed (optional)
        """
        ...

    async def search(self, query: str, k: int = 5, lang: str = "en") -> dict[str, Any]:
        """Perform a web search.

        Args:
            query: Search query string.
            k: Maximum number of results to return.
            lang: Language code for search results.

        Returns:
            Dictionary containing:
                - query: The search query
                - results: List of result dicts with title, url, snippet
        """
        ...

    async def research(
        self, query: str, k: int = 5, model: str = "gpt-3.5-turbo"
    ) -> dict[str, Any]:
        """Perform research combining search and summarization.

        Args:
            query: Research query.
            k: Number of sources to consider.
            model: LLM model to use for summarization.

        Returns:
            Dictionary containing:
                - query: The research query
                - sources: List of source URLs
                - summary: Summarized findings
        """
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


__all__ = ["IFetcher"]
