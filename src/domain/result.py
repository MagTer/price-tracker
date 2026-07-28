"""Shared result types for price and product-metadata extraction."""

from dataclasses import dataclass
from decimal import Decimal


class StoreBlockedError(Exception):
    """A store's bot wall answered instead of data — the HOST is blocking us, not one URL.

    Raised by extractors whose HTTP call hits a WAF (HTTP 202/403/429), because returning
    None there is indistinguishable from "product not found": the ladder would fall through
    to the LLM on an empty page, the check would end as a routine "no_price", and the callers
    would record a SUCCESS against the circuit breaker — resetting the escalation for a store
    that is actively walling us. An exception is the only return channel that survives the
    whole extraction ladder without threading a flag through every result type.
    """


@dataclass
class ProductMetadata:
    """What a product PAGE says the product is — used by quick-add, never by price checks.

    Quick-add needs identity fields (name, brand, category) that PriceExtractionResult
    deliberately does not carry: a price check already knows what product it is checking.
    `price_sek` here is preview display only — the recorded first price always comes from
    the normal perform_price_check flow, so quick-add cannot become a second write path.
    """

    name: str | None
    brand: str | None
    category: str | None
    price_sek: Decimal | None
    package_amount: Decimal | None
    package_unit: str | None
    pack_size: int | None
    confidence: float
    source: str  # "jsonld" | "llm" | "willys_api"
    in_stock: bool | None = None  # availability when the source states it (store APIs do)


@dataclass
class PriceExtractionResult:
    """Result from price extraction."""

    price_sek: Decimal | None
    store_unit_price_sek: (
        Decimal | None
    )  # The store's PRINTED comparison price — never computed (D-05)
    offer_price_sek: Decimal | None
    offer_type: str | None  # "stammispris", "extrapris", "kampanj", etc.
    offer_details: str | None  # "Kop 2 betala for 1"
    in_stock: bool
    confidence: float
    pack_size: int | None  # Number of items in pack (e.g., 16 for "16-p")
    package_amount: Decimal | None  # Amount of product in the package as printed (0.5, 400, 24)
    package_unit: str | None  # Unit that amount is in: "st", "ml", "l", "g", "kg"
    raw_response: dict[str, str | float | bool | None]


def extraction_source(result: PriceExtractionResult | None) -> str:
    """WHICH ladder tier produced this extraction — THE reader of ``raw_response["source"]``.

    Every extractor stamps its own marker ("willys_api", "rusta_page", "clasohlson_page",
    "jsonld"); the LLM path stamps "llm:<model>". The value is deliberately raw rather than
    bucketed into a tier: which MODEL answered is what makes LLM spend readable, and grouping
    is a display decision.

    It exists because the same `isinstance(raw, dict)` dance had been written three times —
    the scheduler's per-tier counters, the JSON-LD enrichment trigger in perform_price_check,
    and the check log — over a loosely-typed JSONB dict where a typo returns None and simply
    reads as "the LLM answered". One definition, like pricing and schedule.

    An ENRICHED JSON-LD result still reports "jsonld": enrich_with_llm keeps the base
    raw_response precisely so a routine enrichment cannot masquerade as an LLM extraction.
    """
    if result is None:
        return "unknown"
    raw = result.raw_response
    if not isinstance(raw, dict):
        return "unknown"
    source = raw.get("source")
    return source if isinstance(source, str) and source else "unknown"
