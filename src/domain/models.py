"""Price Tracker ORM models."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SQLColumnExpression,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.pricing import unit_price_expr, unit_price_py
from infra.db import Base, _utc_now


class Store(Base):
    """Retail store or pharmacy that sells products.

    Represents a vendor where products can be purchased and prices tracked.
    """

    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    store_type: Mapped[str] = mapped_column(String(20))  # grocery, pharmacy, etc.
    base_url: Mapped[str] = mapped_column(String(255))
    parser_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The chain's DEFAULT check schedule, inherited by every link whose own schedule
    # fields are NULL (domain.schedule.effective_schedule — the single resolution rule).
    # Weekdays (0=Mon) are the chain's offer-cycle days: ICA publishes Mondays, Willys
    # Mondays AND Fridays — hence a list, not a single day. Empty/NULL = interval mode
    # every check_frequency_hours, morning-aligned. A chain property because politeness
    # toward the site is a property of the SITE, not of what we track on it.
    check_weekdays: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    check_frequency_hours: Mapped[int] = mapped_column(Integer, default=72)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    # Relationships
    product_stores: Mapped[list["ProductStore"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class Product(Base):
    """The abstract good, tracked across multiple stores.

    Name, brand, category, and `unit` — the canonical comparison unit (st / liter / kg) that
    makes this good's per-store package listings comparable with one another. The product
    itself carries NO package data: the concrete package listing (a 24-pack at Willys, an
    8-pack at ICA) lives on ProductStore, which is what actually describes it.
    Multi-tenant: scoped to tenant_id.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # kg, liter, st
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    product_stores: Mapped[list["ProductStore"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    watches: Mapped[list["PriceWatch"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductStore(Base):
    """The concrete package listing of a product at one store — the "link".

    A store URL plus the package it sells at that URL, with tracking metadata. One product may
    hold SEVERAL links at the same store (a 24-pack and an 8-pack of the same toilet paper are
    two listings of one good), which is why (product_id, store_id) is deliberately NOT unique.
    The URL is the link's natural key — a link *is* a store page — and is therefore globally
    unique: the same page cannot be tracked twice, nor attached to two different products.
    """

    __tablename__ = "product_stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    store_url: Mapped[str] = mapped_column(String(512))
    store_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Display label that disambiguates several LINKS under one Store row ("ICA Maxi
    # Sandviken" vs "ICA Supermarket Björksätra"): ICA prices per physical butik, and both
    # butiker share the chain-level Store whose slug drives the extractors. A label, not a
    # second Store row, on purpose — see link_store_name() below.
    store_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The human display label ("24-pack", "500 ml").
    package_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The amount in the package, in the product's canonical unit. Every kr/unit in the app is
    # computed from this number. Nullable is deliberate (D-02): a link is saveable before its
    # pack size is known, and the first successful scrape autofills it.
    package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # The page's OWN reading of that amount (D-09), rewritten on every check. The operator's
    # typed package_quantity is intent; this is evidence, and it never overwrites intent. The
    # mismatch flag is DERIVED from the two (domain.pricing.quantity_mismatch), never stored,
    # so it self-clears the moment either number is corrected.
    scraped_package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Per-link schedule OVERRIDE. Both NULL (the normal state — quick-add creates links
    # this way) means the link inherits its store's schedule; setting either field takes
    # over wholesale (domain.schedule.effective_schedule). Kept for the legitimate case
    # a store default cannot express: a watched product earning a tighter cadence.
    check_frequency_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_weekdays: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)  # 0=Mon
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="product_stores")
    store: Mapped["Store"] = relationship(back_populates="product_stores")
    price_points: Mapped[list["PricePoint"]] = relationship(
        back_populates="product_store", cascade="all, delete-orphan"
    )

    # A NAMED UniqueConstraint, not `unique=True` on the column: the latter produces a unique
    # INDEX, a different object, and the migration DDL must produce a byte-identical constraint
    # set or `alembic check` reports drift.
    __table_args__ = (UniqueConstraint("store_url", name="uq_product_stores_store_url"),)

    def __repr__(self) -> str:
        return (
            f"<ProductStore(id={self.id}, product_id={self.product_id}, "
            f"store_id={self.store_id}, is_active={self.is_active}, "
            f"next_check_at={self.next_check_at})>"
        )


def link_store_name(product_store: "ProductStore", store: "Store") -> str:
    """THE display store name for a link: its own label when set, else the chain name.

    Every surface that prints a store name next to link data (links panel, history
    series, deals, alert emails, MCP) must go through this — otherwise two ICA-butik
    links render as an indistinguishable "ICA" twice. A plain function taking both
    objects, not a ProductStore property: the property would lazy-load `store` and
    raise MissingGreenlet on any link fetched without a joinedload.

    Deliberately NOT a second Store row per butik: store.slug drives the parser
    hints, the extractor registry, and the migration seeds — per-butik rows would
    multiply that machinery to solve what is purely a naming problem.
    """
    return product_store.store_label or store.name


class PricePoint(Base):
    """A single price observation for a link at a specific time.

    Stores historical price data for trend analysis and alerts. Unit price is NOT stored here:
    it is computed on read from the link's package_quantity (D-03/D-04), so correcting a link's
    quantity retroactively fixes all of its history and a stale snapshot cannot exist.
    """

    __tablename__ = "price_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_stores.id"), index=True)
    price_sek: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # The store's PRINTED comparison price (jämförpris), exactly as scraped (D-05). It is a raw
    # signal shown BESIDE the computed value and is NEVER sorted on: stores print unit prices
    # with incompatible definitions (kr/rulle vs kr/pack vs kr/100g), so ranking on it would
    # compare kronor-per-roll against kronor-per-hectogram. Its worth is exactly that it can
    # disagree with our computed value — that disagreement is what exposes a bad quantity.
    store_unit_price_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    offer_price_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Offer type: campaign, member, etc.
    offer_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    offer_details: Mapped[str | None] = mapped_column(String(255), nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Relationships
    product_store: Mapped["ProductStore"] = relationship(back_populates="price_points")

    @hybrid_property
    def effective_price_sek(self) -> Decimal | None:
        """The price actually paid: the offer when there is one, else the regular price."""
        return self.offer_price_sek if self.offer_price_sek is not None else self.price_sek

    @effective_price_sek.inplace.expression
    @classmethod
    def _effective_price_sek_expr(cls) -> SQLColumnExpression[Decimal | None]:
        return func.coalesce(cls.offer_price_sek, cls.price_sek)

    @hybrid_property
    def computed_unit_price_sek(self) -> Decimal | None:
        """kr/unit — computed, never scraped (D-03). None when the link has no amount (D-02).

        Named `computed_unit_price_sek` and NOT `unit_price_sek` on purpose: reusing the dropped
        column's name would let every consumer we failed to update keep working silently while
        returning a value with a DIFFERENT definition (computed vs store-printed). With a new
        name, a missed reference is a loud AttributeError at the first test run.

        HAZARD: this Python side reads self.product_store. On a PricePoint loaded WITHOUT
        joinedload(PricePoint.product_store), that triggers a lazy load in a non-greenlet context
        and raises MissingGreenlet at request time — mocked tests will not catch it. Where a query
        already selects ProductStore in the same row tuple, call unit_price_py() directly with the
        in-scope link instead of touching this hybrid.
        """
        link = self.product_store
        return unit_price_py(
            self.effective_price_sek, link.package_quantity if link is not None else None
        )

    @computed_unit_price_sek.inplace.expression
    @classmethod
    def _computed_unit_price_sek_expr(cls) -> SQLColumnExpression[Decimal | None]:
        """SQL side: a correlated scalar subquery, so it works with no explicit join.

        HAZARD: in a query that ALREADY joins product_stores, this still emits its own correlated
        subquery — correct, but a redundant re-lookup of the same row. Prefer inline
        unit_price_expr(...) there and reserve this hybrid for SQL contexts with no join.
        """
        return (
            select(unit_price_expr(cls.effective_price_sek, ProductStore.package_quantity))
            .where(ProductStore.id == cls.product_store_id)
            .correlate(cls)
            .scalar_subquery()
        )


class PriceWatch(Base):
    """User's price watch configuration for a product.

    Tracks user preferences for price alerts and notifications.
    Multi-tenant: scoped to tenant_id.
    """

    __tablename__ = "watches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    target_price_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    alert_on_any_offer: Mapped[bool] = mapped_column(Boolean, default=False)
    price_drop_threshold_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_target_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit_price_drop_threshold_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_address: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="watches")
