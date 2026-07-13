"""Protocol for web fetching and search services."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IFetcher(Protocol):
    """Abstract interface for fetching web content."""

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch content from a URL.

        Args:
            url: The URL to fetch content from.

        Returns:
            Dictionary containing:
                - url: The fetched URL
                - ok: Boolean success indicator
                - text: Extracted text content
                - html: Raw page HTML (for structured extraction)
                - error: Error message if failed (optional)
        """
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


__all__ = ["IFetcher"]
