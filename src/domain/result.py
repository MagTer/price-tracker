"""Shared result types for price and product-metadata extraction."""

from dataclasses import dataclass
from decimal import Decimal


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
    source: str  # "jsonld" | "llm"


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
