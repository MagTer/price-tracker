"""Base protocols for price extractors, and the helpers a store-HTML tier shares."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain.result import PriceExtractionResult, ProductMetadata


# THE reader of schema.org blocks out of raw HTML. It had grown two independent copies
# (parser.py's identity-signal scan and jsonld.py's Product walk) before a third store
# needed one — the Gotcha-4 shape, on a pattern where a drifting copy fails by finding
# NOTHING and reading exactly like "this page has no JSON-LD".
LDJSON_BLOCK_RE = re.compile(
    r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)


def format_sek(amount: Decimal) -> str:
    """An öre-quantized amount for a USER-FACING string — Swedish comma decimal, no
    trailing zeros ("3,39", "5").

    The extractors grew three private near-copies of this (Willys with the comma,
    Rusta/Clas Ohlson with a dot); new user-visible savings text goes through this one.
    """
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(quantized, "f").rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def read_json_object(text: str, start: int) -> str | None:
    """The balanced ``{...}`` starting at ``start``, string-escape aware, or None.

    Hydration state is assigned to a ``window.*`` global, not served as a standalone
    document, so its end is found by counting braces rather than by a closing tag. The
    string-escape awareness is the whole point: a product description containing ``{``
    or an escaped quote would otherwise truncate the object mid-way and the parse would
    fail on a page that is perfectly fine.

    Shared by every Avensia Nitro storefront we read (Rusta, Lyko) — one scanner, since
    a second copy is a second place for the escape handling to be subtly wrong.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


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
