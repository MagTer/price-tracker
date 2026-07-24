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
    """Schema for updating an existing product.

    `unit` is deliberately absent: it is the scale every link's package_quantity and the
    whole kr/unit history are expressed in, so changing it would silently misscale all of
    them. Changing unit = delete the product and recreate it.
    """

    name: str | None = None
    brand: str | None = None
    category: str | None = None


class ProductStoreLink(BaseModel):
    """Schema for linking a product to a store.

    The link owns the packaging: a 24-pack at Willys and an 8-pack at Willys are two links
    to one product. `package_quantity` is expressed in the product's canonical unit and may
    be None — the first scrape autofills it (D-02).
    """

    store_id: str
    store_url: str
    # Schedule OVERRIDE — None (default) inherits the store's schedule, which is the
    # normal state. Setting either field takes over wholesale (domain.schedule).
    check_frequency_hours: int | None = None
    check_weekdays: list[int] | None = None  # 0=Monday .. 6=Sunday
    package_size: str | None = None  # Human-readable label: "24-pack", "500 ml", "1 kg"
    package_quantity: float | None = None  # In the product's canonical unit: 24, 0.5, 1.0
    # Per-link store display name ("ICA Maxi Sandviken") for chains with per-butik pricing.
    # None = use the chain name.
    store_label: str | None = None


class ProductStoreUpdate(BaseModel):
    """Schema for editing a link's packaging (D-11).

    Deliberately exposes NEITHER store_id NOR store_url. The URL is the link's identity now;
    re-pointing a link at a different page would silently rewrite the meaning of its entire
    price history. Delete the link and create a new one instead.
    """

    package_size: str | None = None
    package_quantity: float | None = None
    # Per-link store display name — editable here because the butik does not change the
    # link's identity (the URL does); relabeling never rewrites history's meaning.
    store_label: str | None = None


class QuickAddPreview(BaseModel):
    """Schema for previewing a quick-add: one pasted product URL, nothing else.

    The response is a suggestion bundle (matched store, extracted name/brand/price, package
    guess, possible existing products) that the UI renders as an EDITABLE confirm step —
    nothing is persisted by a preview.
    """

    url: str


class QuickAddCreate(BaseModel):
    """Schema for confirming a quick-add: create the product AND its link in one call.

    `product_id` set means "this URL is a new LINK on an existing product" — the 04.1 model's
    normal case for a second pack size — and the identity fields (name/brand/category/unit)
    are then ignored. `product_id` empty means "create a new product first".

    Package fields follow the same rules as ProductStoreLink: quantity is in the product's
    canonical unit and may be None (the first scrape autofills it, D-02/D-07).
    """

    url: str
    store_id: str
    product_id: str | None = None  # link to an existing product instead of creating one
    name: str | None = None  # required when product_id is None
    brand: str | None = None
    category: str | None = None
    unit: str | None = None  # canonical comparison unit: "st", "liter", "kg"
    package_size: str | None = None
    package_quantity: float | None = None
    store_label: str | None = None  # per-link store display name ("ICA Maxi Sandviken")
    # Default False: adding a product must not fetch. A new link is immediately due
    # (next_check_at is NULL), so the scheduler takes its first price on the next cycle,
    # through the SAME per-store politeness ledger — no burst of same-host requests during
    # add, which is what tripped ICA's WAF. Set True only for a deliberate immediate check
    # of a store that does not rate-limit.
    run_first_check: bool = False
    # Also create the link(s) for the pasted butik's sibling stores (SIBLING_STORE_GROUPS),
    # derived server-side from `url`. Siblings are created optimistically — the scheduler
    # checks them like any link. (They are NOT fetched during add: same-store_id sister
    # butiker share one WAF/host, so verifying each inline was exactly the burst to avoid,
    # and under the block-aware fetcher a rate-limited fetch is indistinguishable from a
    # genuinely absent page, so inline verification would wrongly delete valid siblings.)
    add_siblings: bool = False


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
    and a flag the UI must render as a warning arrives as a number. The nested list/dict
    arms carry the schedule fields (`check_weekdays` is a weekday LIST, `store_schedule`
    an object) — v0.13.0.
    """

    id: str
    name: str
    brand: str | None
    category: str | None
    unit: str | None
    stores: list[dict[str, str | int | float | bool | list[int] | dict[str, object] | None]]


class PricePointResponse(BaseModel):
    """Schema for a single price point in history.

    Two different numbers with two different definitions:
    - `unit_price_sek` is the COMPUTED value (effective price / the link's package_quantity).
      This is the comparable one — the only one that may be sorted or ranked on.
    - `store_unit_price_sek` is what the STORE PRINTED (its jämförpris), verbatim and possibly
      in a different unit than ours. Display only; NEVER sort on it (D-05).

    A history row is an observation on one LINK, not on a product: a product may carry a
    16-pack and a 24-pack at the SAME store. `product_store_id` is what makes the rows
    separable — without it the client can only group by store, which merges those two into one
    series and reports a bigger box as a price rise. That is the whole bug (D-12, QD-1).
    """

    checked_at: str
    product_store_id: str  # The LINK this observation belongs to — the grouping key
    store_name: str
    store_slug: str
    package_size: str | None  # The link's printed label, e.g. "24-pack"
    package_quantity: float | None  # None => this link HAS no kr/unit; never coerce it to 0
    price_sek: float | None
    unit_price_sek: float | None  # COMPUTED — the sortable one
    store_unit_price_sek: float | None  # What the store printed — display only
    offer_price_sek: float | None
    offer_type: str | None
    offer_details: str | None
    in_stock: bool


class DealResponse(BaseModel):
    """Schema for current deals/offers — one row per LINK.

    A deal is an offer on a store LISTING, not on a product-at-a-store: a 24-pack and an
    8-pack of one product at one store are two links and may each be on offer.

    `unit_price_sek` is the COMPUTED kr/unit (D-03), exposed so a consumer can compare packs.
    Deals themselves are still ordered by RECENCY — the number is data here, not the ranking.

    `best_alt_*` is the cheapest CURRENT jfr-pris among the product's OTHER links (any
    store, any pack size) — the number that turns a discount percentage into a decision:
    a 20% ICA offer can still be pricier per unit than Willys ordinarie.
    """

    product_id: str
    product_name: str
    store_name: str
    store_slug: str
    package_size: str | None  # The link's label: "24-pack", "500 ml"
    unit: str | None  # The product's comparison unit (st/liter/kg) — labels the jfr-pris
    price_sek: float | None
    offer_price_sek: float
    unit_price_sek: float | None  # COMPUTED — None when the link has no amount yet (D-02)
    offer_type: str
    offer_details: str | None
    checked_at: str
    discount_percent: float
    product_url: str
    best_alt_unit_price_sek: float | None
    best_alt_store: str | None
    best_alt_package_size: str | None


__all__ = [
    "ProductCreate",
    "ProductUpdate",
    "ProductStoreLink",
    "ProductStoreUpdate",
    "QuickAddPreview",
    "QuickAddCreate",
    "PriceWatchCreate",
    "PriceWatchUpdate",
    "StoreResponse",
    "ProductResponse",
    "PricePointResponse",
    "DealResponse",
]
