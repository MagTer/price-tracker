"""Base protocols for price extractors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain.result import PriceExtractionResult, ProductMetadata


@runtime_checkable
class PriceExtractor(Protocol):
    """Protocol for store-API extractors (the ladder's first tier).

    Makes its own HTTP call, so implementations must ride the shared ledger and raise
    StoreBlockedError on a bot wall. Returns PriceExtractionResult on success, None to
    signal fallback to the next tier.
    """

    async def extract(
        self, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None: ...


@runtime_checkable
class HtmlPriceExtractor(Protocol):
    """Protocol for store-HTML extractors (the ladder's second tier, v0.31.0).

    Parses the page the pipeline ALREADY fetched — no HTTP calls of its own, no ledger
    slot, and it can never raise StoreBlockedError. Both methods return None to signal
    fallback to the JSON-LD tier; sync on purpose, there is nothing to await.
    """

    def extract_from_html(
        self, html: str, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None: ...

    def extract_metadata_from_html(self, html: str, store_url: str) -> ProductMetadata | None: ...
