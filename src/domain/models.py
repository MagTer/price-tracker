"""Price Tracker ORM models."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    # Relationships
    product_stores: Mapped[list["ProductStore"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class Product(Base):
    """A product that can be tracked across multiple stores.

    Represents a specific SKU including package size (e.g., 'Toalettpapper 24-pack').
        Different package sizes are separate products that can independently link to stores.
        Multi-tenant: scoped to tenant_id.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # kg, liter, st, etc.
    package_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
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
    """Association between a product and a store with tracking metadata.

    Links a generic product to a specific store implementation with URL and scraping config.
    """

    __tablename__ = "product_stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    store_url: Mapped[str] = mapped_column(String(512))
    store_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_frequency_hours: Mapped[int] = mapped_column(Integer, default=72)
    check_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon, 6=Sun
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="product_stores")
    store: Mapped["Store"] = relationship(back_populates="product_stores")
    price_points: Mapped[list["PricePoint"]] = relationship(
        back_populates="product_store", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("product_id", "store_id", name="uq_product_store"),)

    def __repr__(self) -> str:
        return (
            f"<ProductStore(id={self.id}, product_id={self.product_id}, "
            f"store_id={self.store_id}, is_active={self.is_active}, "
            f"next_check_at={self.next_check_at})>"
        )


class PricePoint(Base):
    """A single price observation for a product at a store at a specific time.

    Stores historical price data for trend analysis and alerts.
    """

    __tablename__ = "price_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_stores.id"), index=True
    )
    price_sek: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    offer_price_sek: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Offer type: campaign, member, etc.
    offer_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    offer_details: Mapped[str | None] = mapped_column(String(255), nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Relationships
    product_store: Mapped["ProductStore"] = relationship(back_populates="price_points")


class PriceWatch(Base):
    """User's price watch configuration for a product.

    Tracks user preferences for price alerts and notifications.
    Multi-tenant: scoped to tenant_id.
    """

    __tablename__ = "watches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), index=True
    )
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
