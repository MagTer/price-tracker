"""Base protocol for price extractors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain.result import PriceExtractionResult


@runtime_checkable
class PriceExtractor(Protocol):
    """Protocol for store-specific price extractors.

    Returns PriceExtractionResult on success, None to signal LLM fallback.
    """

    async def extract(
        self, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None: ...
