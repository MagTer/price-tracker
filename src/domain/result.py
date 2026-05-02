"""Shared result type for price extraction."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PriceExtractionResult:
    """Result from price extraction."""

    price_sek: Decimal | None
    unit_price_sek: Decimal | None
    offer_price_sek: Decimal | None
    offer_type: str | None  # "stammispris", "extrapris", "kampanj", etc.
    offer_details: str | None  # "Kop 2 betala for 1"
    in_stock: bool
    confidence: float
    pack_size: int | None  # Number of items in pack (e.g., 16 for "16-p")
    raw_response: dict[str, str | float | bool | None]
