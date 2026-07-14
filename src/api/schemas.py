"""Pydantic schemas for price tracker API."""

from decimal import Decimal

from pydantic import BaseModel


class ProductCreate(BaseModel):
    """Schema for creating a new product — the abstract good, not a package.

    A product is "Lambi toalettpapper", not "Lambi toalettpapper 24-pack". It carries only
    name, brand, category, and `unit` — the canonical comparison unit (st / liter / kg) that
    makes its per-store package listings comparable with one another.

    Package data (the 24-pack, the 500 ml bottle) belongs to the STORE LINK: the same good is
    sold in different package sizes at different stores, and at more than one size in the same
    store. See ProductStoreLink / ProductStoreUpdate.
    """

    tenant_id: str
    name: str
    brand: str | None = None
    category: str | None = None
    unit: str | None = None  # Canonical comparison unit: "st", "liter", "kg"


class ProductUpdate(BaseModel):
    """Schema for updating an existing product."""

    name: str | None = None
    brand: str | None = None
    category: str | None = None
    unit: str | None = None


class ProductStoreLink(BaseModel):
    """Schema for linking a product to a store.

    The link owns the packaging: a 24-pack at Willys and an 8-pack at Willys are two links
    to one product. `package_quantity` is expressed in the product's canonical unit and may
    be None — the first scrape autofills it (D-02).
    """

    store_id: str
    store_url: str
    check_frequency_hours: int = 72
    check_weekday: int | None = None  # 0=Monday, 6=Sunday, None=use frequency
    package_size: str | None = None  # Human-readable label: "24-pack", "500 ml", "1 kg"
    package_quantity: float | None = None  # In the product's canonical unit: 24, 0.5, 1.0


class ProductStoreUpdate(BaseModel):
    """Schema for editing a link's packaging (D-11).

    Deliberately exposes NEITHER store_id NOR store_url. The URL is the link's identity now;
    re-pointing a link at a different page would silently rewrite the meaning of its entire
    price history. Delete the link and create a new one instead.
    """

    package_size: str | None = None
    package_quantity: float | None = None


class PriceWatchCreate(BaseModel):
    """Schema for creating a price watch."""

    product_id: str
    target_price_sek: Decimal | None = None
    alert_on_any_offer: bool = False
    price_drop_threshold_percent: int | None = None
    unit_price_target_sek: Decimal | None = None
    unit_price_drop_threshold_percent: int | None = None
    email_address: str


class PriceWatchUpdate(BaseModel):
    """Schema for updating a price watch."""

    target_price_sek: Decimal | None = None
    alert_on_any_offer: bool | None = None
    price_drop_threshold_percent: int | None = None
    unit_price_target_sek: Decimal | None = None
    unit_price_drop_threshold_percent: int | None = None
    email_address: str | None = None


class StoreResponse(BaseModel):
    """Schema for store data response."""

    id: str
    name: str
    slug: str
    store_type: str
    base_url: str
    is_active: bool


class ProductResponse(BaseModel):
    """Schema for product data response with linked stores.

    Package data is NOT here — each dict in `stores` carries its own link's package_size,
    package_quantity, scraped_package_quantity, both unit prices, and the two derived flags
    (`needs_amount`, `quantity_mismatch`).

    `bool` is in the value union deliberately: without it, a smart-mode union of
    `str | int | float` coerces `needs_amount=True` to the integer `1` (bool subclasses int),
    and a flag the UI must render as a warning arrives as a number.
    """

    id: str
    name: str
    brand: str | None
    category: str | None
    unit: str | None
    stores: list[dict[str, str | int | float | bool | None]]


class PricePointResponse(BaseModel):
    """Schema for a single price point in history.

    Two different numbers with two different definitions:
    - `unit_price_sek` is the COMPUTED value (effective price / the link's package_quantity).
      This is the comparable one — the only one that may be sorted or ranked on.
    - `store_unit_price_sek` is what the STORE PRINTED (its jämförpris), verbatim and possibly
      in a different unit than ours. Display only; NEVER sort on it (D-05).
    """

    checked_at: str
    store_name: str
    store_slug: str
    price_sek: float | None
    unit_price_sek: float | None  # COMPUTED — the sortable one
    store_unit_price_sek: float | None  # What the store printed — display only
    offer_price_sek: float | None
    offer_type: str | None
    offer_details: str | None
    in_stock: bool


class DealResponse(BaseModel):
    """Schema for current deals/offers."""

    product_id: str
    product_name: str
    store_name: str
    store_slug: str
    price_sek: float | None
    offer_price_sek: float
    offer_type: str
    offer_details: str | None
    checked_at: str
    discount_percent: float
    product_url: str


__all__ = [
    "ProductCreate",
    "ProductUpdate",
    "ProductStoreLink",
    "ProductStoreUpdate",
    "PriceWatchCreate",
    "PriceWatchUpdate",
    "StoreResponse",
    "ProductResponse",
    "PricePointResponse",
    "DealResponse",
]
