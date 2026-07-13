"""Admin API endpoints for price tracker module."""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db import async_session_factory
from infra.providers import get_fetcher
from api.auth import require_auth
from fastapi.responses import HTMLResponse
from api.schemas import (
    DealResponse,
    PricePointResponse,
    PriceWatchCreate,
    PriceWatchUpdate,
    ProductCreate,
    ProductResponse,
    ProductStoreLink,
    ProductUpdate,
    StoreResponse
)
from domain.models import PricePoint, PriceWatch, Product, ProductStore, Store
from domain.parser import PriceParser
from domain.service import PriceTrackerService
from domain.tenant import DEFAULT_TENANT_ID

LOGGER = logging.getLogger(__name__)

# No path prefix: the /admin prefix was a holdover from the source platform
# where OpenWebUI owned "/" — standalone, this UI+API is the whole app.
router = APIRouter(
    tags=["price-tracker"],
    dependencies=[Depends(require_auth)],
)


def get_price_tracker_service() -> PriceTrackerService:
    """Get PriceTrackerService instance."""
    return PriceTrackerService(async_session_factory)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def require_default_tenant(tenant_id: str) -> uuid.UUID:
    """Validate a client-supplied tenant id against the single-tenant constant.

    Raises:
        HTTPException 400: malformed UUID.
        HTTPException 403: valid UUID but not the seeded tenant.
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid tenant_id format") from e
    if tenant_uuid != DEFAULT_TENANT_ID:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only act within your own context",
        )
    return tenant_uuid


def sanitize_log(value: Any) -> str:
    """Sanitize a value for safe logging."""
    if value is None:
        return "None"
    s = str(value)
    if len(s) > 64:
        s = s[:61] + "..."
    return s


@router.get(
    "/stores", response_model=list[StoreResponse]
)
async def list_stores(session: AsyncSession = Depends(get_db)) -> list[StoreResponse]:
    """List all configured stores.
        List of store information including slug, type, and status.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        stmt = select(Store).where(Store.is_active.is_(True)).order_by(Store.name)
        result = await session.execute(stmt)
        stores = result.scalars().all()

        return [
            StoreResponse(
                id=str(store.id),
                name=store.name,
                slug=store.slug,
                store_type=store.store_type,
                base_url=store.base_url,
                is_active=store.is_active,
            )
            for store in stores
        ]
    except Exception as e:
        LOGGER.exception("Failed to list stores")
        raise HTTPException(status_code=500, detail="Failed to list stores") from e


@router.get("/products", response_model=list[ProductResponse])
async def list_products(
    search: str | None = None,
    store_id: str | None = None,
    tenant_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[ProductResponse]:
    """List products with optional search/filter.

    Args:
        search: Search term for product name or brand.
        store_id: Filter by specific store UUID.
        tenant_id: Filter by user context UUID (shows only products with watches in that context).
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        List of products with linked stores.

    Security:
        Requires admin role via Entra ID authentication.
        Users can only query their own tenant_id.
    """
    try:
        # Security check: if tenant_id provided, verify user has access to it
        if tenant_id:
            try:
                tenant_uuid = uuid.UUID(tenant_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid tenant_id format") from e

            # Verify user can only query their own context
            if tenant_uuid != DEFAULT_TENANT_ID:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: you can only view products in your own context",
                )

        # Build query with proper join handling
        stmt = select(Product)

        # Apply context filter: show products owned by this context
        if tenant_id:
            tenant_uuid = uuid.UUID(tenant_id)
            stmt = stmt.where(Product.tenant_id == tenant_uuid)

        # Apply search filter
        if search:
            from sqlalchemy import or_

            search_term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Product.name.ilike(search_term),
                    Product.brand.ilike(search_term),
                )
            )

        # Apply store filter
        if store_id:
            try:
                store_uuid = uuid.UUID(store_id)
                stmt = stmt.join(ProductStore, Product.id == ProductStore.product_id)
                stmt = stmt.where(ProductStore.store_id == store_uuid).distinct()
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid store_id format") from e

        stmt = stmt.order_by(Product.name)
        result = await session.execute(stmt)
        products = result.scalars().all()

        # Batch-fetch store links and latest prices (avoids N+1 per product)
        product_ids = [product.id for product in products]
        ps_by_product: dict[uuid.UUID, list[tuple[ProductStore, Store]]] = {}
        latest_by_ps: dict[uuid.UUID, PricePoint] = {}
        if product_ids:
            ps_stmt = (
                select(ProductStore, Store)
                .join(Store, ProductStore.store_id == Store.id)
                .where(ProductStore.product_id.in_(product_ids))
            )
            ps_result = await session.execute(ps_stmt)
            for ps, store in ps_result.all():
                ps_by_product.setdefault(ps.product_id, []).append((ps, store))

            ps_ids = [ps.id for rows in ps_by_product.values() for ps, _ in rows]
            if ps_ids:
                from sqlalchemy import func
                from sqlalchemy.orm import aliased

                rn = (
                    func.row_number()
                    .over(
                        partition_by=PricePoint.product_store_id,
                        order_by=PricePoint.checked_at.desc(),
                    )
                    .label("rn")
                )
                latest_subq = (
                    select(PricePoint, rn)
                    .where(PricePoint.product_store_id.in_(ps_ids))
                    .subquery()
                )
                latest_pp = aliased(PricePoint, latest_subq)
                latest_result = await session.execute(
                    select(latest_pp).where(latest_subq.c.rn == 1)
                )
                for pp in latest_result.scalars().all():
                    latest_by_ps[pp.product_store_id] = pp

        product_responses: list[ProductResponse] = []
        for product in products:
            stores_data: list[dict[str, str | int | None | float]] = []
            for ps, store in ps_by_product.get(product.id, []):
                latest_price = latest_by_ps.get(ps.id)

                store_data: dict[str, str | int | None | float] = {
                    "product_store_id": str(ps.id),
                    "store_id": str(ps.store_id),
                    "store_name": store.name,
                    "store_slug": store.slug,
                    "store_url": ps.store_url,
                    "check_frequency_hours": ps.check_frequency_hours,
                    "check_weekday": ps.check_weekday,
                    "last_checked_at": (
                        ps.last_checked_at.isoformat() if ps.last_checked_at else None
                    ),
                    "price_sek": (
                        float(latest_price.price_sek)
                        if latest_price and latest_price.price_sek
                        else None
                    ),
                    "unit_price_sek": (
                        float(latest_price.unit_price_sek)
                        if latest_price and latest_price.unit_price_sek
                        else None
                    ),
                    "in_stock": latest_price.in_stock if latest_price else None,
                }
                stores_data.append(store_data)

            product_responses.append(
                ProductResponse(
                    id=str(product.id),
                    name=product.name,
                    brand=product.brand,
                    category=product.category,
                    unit=product.unit,
                    package_size=product.package_size,
                    package_quantity=(
                        float(product.package_quantity) if product.package_quantity else None
                    ),
                    stores=stores_data,
                )
            )

        return product_responses
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list products")
        raise HTTPException(status_code=500, detail="Failed to list products") from e


@router.post("/products", status_code=201, )
async def create_product(
    data: ProductCreate,
    admin_email: str = Depends(require_auth),
    service: PriceTrackerService = Depends(get_price_tracker_service)
) -> dict[str, str]:
    """Create a new product to track.

    Products are scoped to the authenticated user's context for multi-tenancy.
    Different package sizes should be created as separate products.

    Args:
        data: Product creation data.
        admin: Authenticated admin user.
        session: Database session.
        service: Price tracker service.

    Returns:
        Dictionary with product_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        from decimal import Decimal

        # Validate package_quantity if provided
        if data.package_quantity is not None and data.package_quantity <= 0:
            raise HTTPException(status_code=400, detail="package_quantity must be positive")

        tenant_uuid = require_default_tenant(data.tenant_id)
        package_qty = Decimal(str(data.package_quantity)) if data.package_quantity else None

        product = await service.create_product(
            tenant_id=tenant_uuid,
            name=data.name,
            brand=data.brand,
            category=data.category,
            unit=data.unit,
            package_size=data.package_size,
            package_quantity=package_qty,
        )
        return {"product_id": str(product.id), "message": "Product created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to create product")
        raise HTTPException(status_code=500, detail="Failed to create product") from e


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_auth)]
)
async def get_product(
    product_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> ProductResponse:
    """Get a product by ID.

    Args:
        product_id: Product UUID.
        session: Database session.

    Returns:
        Product data with linked stores.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Get linked stores
        ps_stmt = (
            select(ProductStore, Store)
            .join(Store, ProductStore.store_id == Store.id)
            .where(ProductStore.product_id == product.id)
        )
        ps_result = await session.execute(ps_stmt)
        ps_rows = ps_result.all()

        stores_data: list[dict[str, str | int | None | float]] = []
        for ps, store in ps_rows:
            # Get latest price point for this product-store
            price_stmt = (
                select(PricePoint)
                .where(PricePoint.product_store_id == ps.id)
                .order_by(PricePoint.checked_at.desc())
                .limit(1)
            )
            price_result = await session.execute(price_stmt)
            latest_price = price_result.scalar_one_or_none()

            store_data: dict[str, str | int | None | float] = {
                "product_store_id": str(ps.id),
                "store_id": str(ps.store_id),
                "store_name": store.name,
                "store_slug": store.slug,
                "store_url": ps.store_url,
                "check_frequency_hours": ps.check_frequency_hours,
                "check_weekday": ps.check_weekday,
                "last_checked_at": ps.last_checked_at.isoformat() if ps.last_checked_at else None,
                "price_sek": (
                    float(latest_price.price_sek)
                    if latest_price and latest_price.price_sek
                    else None
                ),
                "unit_price_sek": (
                    float(latest_price.unit_price_sek)
                    if latest_price and latest_price.unit_price_sek
                    else None
                ),
                "in_stock": latest_price.in_stock if latest_price else None,
            }
            stores_data.append(store_data)

        return ProductResponse(
            id=str(product.id),
            name=product.name,
            brand=product.brand,
            category=product.category,
            unit=product.unit,
            package_size=product.package_size,
            package_quantity=(
                float(product.package_quantity) if product.package_quantity else None
            ),
            stores=stores_data,
        )
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to get product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to retrieve product") from e


@router.put(
    "/products/{product_id}"
)
async def update_product(
    product_id: str,
    data: ProductUpdate,
    session: AsyncSession = Depends(get_db),
):
    """Update an existing product.

    Args:
        product_id: Product UUID.
        data: Product update data (only provided fields are updated).
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Update only provided fields
        if data.name is not None:
            product.name = data.name
        if data.brand is not None:
            product.brand = data.brand if data.brand else None
        if data.category is not None:
            product.category = data.category if data.category else None
        if data.unit is not None:
            product.unit = data.unit if data.unit else None
        if data.package_size is not None:
            product.package_size = data.package_size if data.package_size else None
        if data.package_quantity is not None:
            from decimal import Decimal

            if data.package_quantity <= 0:
                raise HTTPException(status_code=400, detail="package_quantity must be positive")
            product.package_quantity = Decimal(str(data.package_quantity))

        await session.commit()
        return {"message": "Product updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to update product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to update product") from e


@router.post(
    "/products/{product_id}/stores",
    status_code=201,
    dependencies=[Depends(require_auth)]
)
async def link_product_to_store(
    product_id: str,
    data: ProductStoreLink,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Link a product to a store with URL.

    Args:
        product_id: Product UUID.
        data: Store link data.
        service: Price tracker service.

    Returns:
        Dictionary with product_store_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Validate frequency range (3 days to 10 days)
    if not (72 <= data.check_frequency_hours <= 240):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 72 and 240 (inclusive)",
        )
    # Validate weekday if provided (0=Monday, 6=Sunday)
    if data.check_weekday is not None and not (0 <= data.check_weekday <= 6):
        raise HTTPException(
            status_code=400,
            detail="check_weekday must be between 0 (Monday) and 6 (Sunday)",
        )

    try:
        product_store = await service.link_product_store(
            product_id=product_id,
            store_id=data.store_id,
            store_url=data.store_url,
            check_frequency_hours=data.check_frequency_hours,
            check_weekday=data.check_weekday,
        )
        return {
            "product_store_id": str(product_store.id),
            "message": "Product linked to store successfully",
        }
    except Exception as e:
        LOGGER.exception(
            "Failed to link product %s to store %s",
            sanitize_log(product_id),
            sanitize_log(data.store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to link product to store") from e


@router.put(
    "/products/{product_id}/stores/{store_id}/frequency",
    dependencies=[Depends(require_auth)]
)
async def update_check_frequency(
    product_id: str,
    store_id: str,
    request: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Update check frequency for a product-store link.

    Args:
        product_id: Product UUID.
        store_id: Store UUID.
        request: Dictionary containing check_frequency_hours.
        session: Database session.

    Returns:
        Success message with updated next_check_at timestamp.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
        store_uuid = uuid.UUID(store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from e

    check_frequency_hours = request.get("check_frequency_hours")
    check_weekday = request.get("check_weekday")  # 0=Monday, 6=Sunday, None=use frequency

    if check_frequency_hours is None:
        raise HTTPException(status_code=400, detail="check_frequency_hours is required")

    # Validate frequency range (3 days to 10 days)
    if not (72 <= check_frequency_hours <= 240):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 72 and 240 (inclusive)",
        )
    # Validate weekday if provided
    if check_weekday is not None and not (0 <= check_weekday <= 6):
        raise HTTPException(
            status_code=400,
            detail="check_weekday must be between 0 (Monday) and 6 (Sunday)",
        )

    try:
        stmt = select(ProductStore).where(
            ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        # Update frequency and weekday
        product_store.check_frequency_hours = check_frequency_hours
        product_store.check_weekday = check_weekday

        # Calculate next_check_at
        now_utc = datetime.now(UTC).replace(tzinfo=None)

        if check_weekday is not None:
            # Weekday-based: schedule for next occurrence of that weekday
            # Spread checks over morning hours (06:00 - 12:00)
            days_until = (check_weekday - now_utc.weekday()) % 7
            if days_until == 0 and now_utc.hour >= 12:
                # Already past check window today, schedule for next week
                days_until = 7
            # Random hour between 6 and 12
            check_hour = 6 + int(random.random() * 6)  # noqa: S311
            check_minute = int(random.random() * 60)  # noqa: S311
            next_check = now_utc.replace(hour=check_hour, minute=check_minute, second=0)
            next_check = next_check + timedelta(days=days_until)
            product_store.next_check_at = next_check
        else:
            # Frequency-based: use jitter as before
            jitter_percent = 0.1
            jitter_hours = (
                (random.random() * 2 - 1) * jitter_percent * check_frequency_hours  # noqa: S311
            )
            product_store.next_check_at = now_utc + timedelta(
                hours=check_frequency_hours + jitter_hours
            )

        await session.commit()
        await session.refresh(product_store)

        return {
            "message": "Frequency updated",
            "next_check_at": (
                product_store.next_check_at.isoformat() if product_store.next_check_at else None
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(
            "Failed to update frequency for product %s, store %s",
            sanitize_log(product_id),
            sanitize_log(store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to update frequency") from e


@router.delete(
    "/products/{product_id}/stores/{store_id}",
    dependencies=[Depends(require_auth)]
)
async def unlink_product_from_store(
    product_id: str | None = None,
    store_id: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    """Remove product-store link.

    Args:
        product_id: Product UUID.
        store_id: Store UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
        store_uuid = uuid.UUID(store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from e

    try:
        stmt = select(ProductStore).where(
            ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        await session.delete(product_store)
        await session.commit()

        return {"message": "Product unlinked from store successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(
            "Failed to unlink product %s from store %s",
            sanitize_log(product_id),
            sanitize_log(store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to unlink product from store") from e


@router.get(
    "/products/{product_id}/prices",
    response_model=list[PricePointResponse],
    dependencies=[Depends(require_auth)]
)
async def get_price_history(
    product_id: str | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_db),
):
    """Get price history for a product across all stores.

    Args:
        product_id: Product UUID.
        days: Number of days of history to retrieve (default 30).
        session: Database session.

    Returns:
        List of price points sorted by checked_at descending.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        from datetime import timedelta

        cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

        stmt = (
            select(PricePoint, Store)
            .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
            .join(Store, ProductStore.store_id == Store.id)
            .where(ProductStore.product_id == product_uuid)
            .where(PricePoint.checked_at >= cutoff_date)
            .order_by(PricePoint.checked_at.desc())
        )

        result = await session.execute(stmt)
        rows = result.all()

        return [
            PricePointResponse(
                checked_at=price_point.checked_at.isoformat(),
                store_name=store.name,
                store_slug=store.slug,
                price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                unit_price_sek=(
                    float(price_point.unit_price_sek) if price_point.unit_price_sek else None
                ),
                offer_price_sek=(
                    float(price_point.offer_price_sek) if price_point.offer_price_sek else None
                ),
                offer_type=price_point.offer_type,
                offer_details=price_point.offer_details,
                in_stock=price_point.in_stock,
            )
            for price_point, store in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to get price history for product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to retrieve price history") from e


@router.post(
    "/check/{product_store_id}"
)
async def trigger_price_check(
    product_store_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | float | None]:
    """Manually trigger a price check for a product-store combination.

    This endpoint:
    1. Fetches the product page using WebFetcher
    2. Extracts price using PriceParser
    3. Records price using PriceTrackerService

    Args:
        product_store_id: ProductStore UUID.
        session: Database session.
        service: Price tracker service.

    Returns:
        Dictionary with extracted price data.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_store_id format") from e

    try:
        # Get ProductStore with joined Store and Product
        stmt = (
            select(ProductStore, Store, Product)
            .join(Store, ProductStore.store_id == Store.id)
            .join(Product, ProductStore.product_id == Product.id)
            .where(ProductStore.id == ps_uuid)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        product_store, store, product = row

        # Fetch page content
        fetcher = get_fetcher()
        fetch_result = await fetcher.fetch(product_store.store_url)

        if not fetch_result.get("ok") or not fetch_result.get("text"):
            error_msg = fetch_result.get("error", "Unknown fetch error")
            raise HTTPException(status_code=502, detail=f"Failed to fetch page: {error_msg}")

        # Parse price data
        parser = PriceParser()
        extraction_result = await parser.extract_price(
            text_content=fetch_result["text"],
            store_slug=store.slug,
            product_name=product.name,
            store_url=product_store.store_url,
            html_content=fetch_result.get("html"),
        )

        if not extraction_result.price_sek:
            return {
                "message": "Price extraction failed - no price found",
                "confidence": extraction_result.confidence,
                "price_sek": None,
                "offer_price_sek": None,
            }

        # Record price
        unit_price = (
            float(extraction_result.unit_price_sek) if extraction_result.unit_price_sek else None
        )
        offer_price = (
            float(extraction_result.offer_price_sek) if extraction_result.offer_price_sek else None
        )

        price_data: dict[str, Any] = {
            "price_sek": float(extraction_result.price_sek),
            "unit_price_sek": unit_price,
            "offer_price_sek": offer_price,
            "offer_type": extraction_result.offer_type,
            "offer_details": extraction_result.offer_details,
            "in_stock": extraction_result.in_stock,
            "raw_data": extraction_result.raw_response,
        }

        price_point = await service.record_price(product_store_id, price_data, session)

        if not price_point:
            raise HTTPException(status_code=500, detail="Failed to record price")

        unit_price_result = (
            float(price_point.unit_price_sek) if price_point.unit_price_sek else None
        )
        offer_price_result = (
            float(price_point.offer_price_sek) if price_point.offer_price_sek else None
        )

        return {
            "message": "Price check completed successfully",
            "price_sek": float(price_point.price_sek),
            "unit_price_sek": unit_price_result,
            "offer_price_sek": offer_price_result,
            "offer_type": price_point.offer_type,
            "in_stock": price_point.in_stock,
            "confidence": extraction_result.confidence,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to trigger price check for %s", sanitize_log(product_store_id))
        raise HTTPException(status_code=500, detail="Failed to trigger price check") from e


@router.get("/deals", response_model=list[DealResponse])
async def get_current_deals(
    store_type: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[DealResponse]:
    """Get current deals.

    Args:
        store_type: Filter by store type (grocery, pharmacy, etc.). Optional.
        session: Database session.

    Returns:
        List of current deals sorted by checked_at descending.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        from datetime import timedelta

        # Get deals from last 24 hours
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)

        stmt = (
            select(PricePoint, Product, Store, ProductStore)
            .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
            .join(Product, ProductStore.product_id == Product.id)
            .join(Store, ProductStore.store_id == Store.id)
            .where(PricePoint.offer_price_sek.is_not(None))
            .where(PricePoint.checked_at >= cutoff)
            .order_by(PricePoint.checked_at.desc())
        )

        if store_type:
            stmt = stmt.where(Store.store_type == store_type)

        result = await session.execute(stmt)
        rows = result.all()

        # Deduplicate by product-store (keep latest)
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        deals: list[DealResponse] = []

        for price_point, product, store, product_store in rows:
            key = (product.id, store.id)
            if key in seen:
                continue
            seen.add(key)

            # Calculate discount percentage
            discount_percent = 0.0
            if price_point.price_sek and price_point.offer_price_sek:
                discount_percent = (
                    (float(price_point.price_sek) - float(price_point.offer_price_sek))
                    / float(price_point.price_sek)
                    * 100
                )

            deals.append(
                DealResponse(
                    product_id=str(product.id),
                    product_name=product.name,
                    store_name=store.name,
                    store_slug=store.slug,
                    price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                    offer_price_sek=float(price_point.offer_price_sek),
                    offer_type=price_point.offer_type or "unknown",
                    offer_details=price_point.offer_details,
                    checked_at=price_point.checked_at.isoformat(),
                    discount_percent=discount_percent,
                    product_url=product_store.store_url,
                )
            )

        return deals
    except Exception as e:
        LOGGER.exception("Failed to get current deals")
        raise HTTPException(status_code=500, detail="Failed to retrieve deals") from e


@router.get("/watches")
async def list_watches(
    tenant_id: str | None = None,
    admin_email: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    """List price watches, optionally filtered by context.

    Args:
        tenant_id: Filter by context UUID. If not provided, defaults to user's context.
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        List of price watch configurations.

    Security:
        Requires admin role via Entra ID authentication.
        Users can only query their own tenant_id.
    """
    try:
        # If no tenant_id provided, use user's default context
        if not tenant_id:
            tenant_id = str(DEFAULT_TENANT_ID)

        stmt = select(PriceWatch, Product).join(Product, PriceWatch.product_id == Product.id)

        if tenant_id:
            try:
                tenant_uuid = uuid.UUID(tenant_id)

                # Security check: verify user has access to this context
                if tenant_uuid != DEFAULT_TENANT_ID:
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied: you can only view watches in your own context",
                    )

                stmt = stmt.where(PriceWatch.tenant_id == tenant_uuid)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid tenant_id format") from e

        stmt = stmt.where(PriceWatch.is_active.is_(True)).order_by(PriceWatch.created_at.desc())

        result = await session.execute(stmt)
        rows = result.all()

        watches_list: list[dict[str, Any]] = []
        for watch, product in rows:
            target_price = float(watch.target_price_sek) if watch.target_price_sek else None
            unit_price_target = (
                float(watch.unit_price_target_sek) if watch.unit_price_target_sek else None
            )
            last_alerted = watch.last_alerted_at.isoformat() if watch.last_alerted_at else None

            watches_list.append(
                {
                    "watch_id": str(watch.id),
                    "tenant_id": str(watch.tenant_id),
                    "product_id": str(watch.product_id),
                    "product_name": product.name,
                    "target_price_sek": target_price,
                    "alert_on_any_offer": watch.alert_on_any_offer,
                    "price_drop_threshold_percent": watch.price_drop_threshold_percent,
                    "unit_price_target_sek": unit_price_target,
                    "unit_price_drop_threshold_percent": watch.unit_price_drop_threshold_percent,
                    "email_address": watch.email_address,
                    "last_alerted_at": last_alerted,
                    "created_at": watch.created_at.isoformat(),
                }
            )

        return watches_list
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list watches")
        raise HTTPException(status_code=500, detail="Failed to list watches") from e


@router.post(
    "/watches", status_code=201
)
async def create_watch(
    data: PriceWatchCreate,
    tenant_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Create a new price watch alert.

    Args:
        data: Price watch configuration.
        tenant_id: Context UUID for multi-tenancy.
        service: Price tracker service.

    Returns:
        Dictionary with watch_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
        tenant_id must match the seeded tenant; email must be well-formed.
    """
    require_default_tenant(tenant_id)
    if not EMAIL_PATTERN.match(data.email_address):
        raise HTTPException(status_code=400, detail="Invalid email address")
    try:
        watch = await service.create_watch(
            tenant_id=tenant_id,
            product_id=data.product_id,
            email=data.email_address,
            target_price=data.target_price_sek,
            alert_on_any_offer=data.alert_on_any_offer,
            price_drop_threshold_percent=data.price_drop_threshold_percent,
            unit_price_target_sek=data.unit_price_target_sek,
            unit_price_drop_threshold_percent=data.unit_price_drop_threshold_percent,
        )
        return {"watch_id": str(watch.id), "message": "Price watch created successfully"}
    except Exception as e:
        LOGGER.exception("Failed to create price watch")
        raise HTTPException(status_code=500, detail="Failed to create price watch") from e


@router.put("/watches/{watch_id}")
async def update_watch(
    watch_id: str,
    data: PriceWatchUpdate,
    session: AsyncSession = Depends(get_db),
):
    """Update a price watch.

    Args:
        watch_id: Watch UUID.
        data: Watch update data (only provided fields are updated).
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid watch_id format") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Price watch not found")

        # Update only provided fields
        if data.target_price_sek is not None:
            watch.target_price_sek = Decimal(str(data.target_price_sek))
        if data.alert_on_any_offer is not None:
            watch.alert_on_any_offer = data.alert_on_any_offer
        if data.price_drop_threshold_percent is not None:
            watch.price_drop_threshold_percent = data.price_drop_threshold_percent
        if data.unit_price_target_sek is not None:
            watch.unit_price_target_sek = Decimal(str(data.unit_price_target_sek))
        if data.unit_price_drop_threshold_percent is not None:
            watch.unit_price_drop_threshold_percent = data.unit_price_drop_threshold_percent
        if data.email_address is not None:
            if not EMAIL_PATTERN.match(data.email_address):
                raise HTTPException(status_code=400, detail="Invalid email address")
            watch.email_address = data.email_address

        await session.commit()
        return {"message": "Price watch updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to update watch %s", sanitize_log(watch_id))
        raise HTTPException(status_code=500, detail="Failed to update price watch") from e


@router.delete(
    "/watches/{watch_id}"
)
async def delete_watch(
    watch_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a price watch.

    Args:
        watch_id: Watch UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid watch_id format") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Price watch not found")

        await session.delete(watch)
        await session.commit()

        return {"message": "Price watch deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to delete watch %s", sanitize_log(watch_id))
        raise HTTPException(status_code=500, detail="Failed to delete price watch") from e


@router.delete(
    "/products/{product_id}"
)
async def delete_product(
    product_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
):
    """Delete a product and all associated data.

    This will cascade delete:
    - ProductStore links
    - PricePoints
    - PriceWatches

    Args:
        product_id: Product UUID.
        service: Price tracker service.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        await service.delete_product(product_id)
        return {"message": "Product deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product ID") from e
    except Exception as e:
        LOGGER.exception("Failed to delete product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to delete product") from e


@router.get("/export")
async def export_data(
    include_history: bool = False,
    history_days: int = 30,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export user's price tracker data as JSON.

    Exports all products, store links, and watches for the user's context.
    Optionally includes price history (can be large).

    Args:
        include_history: Whether to include price history (default: False).
        history_days: Days of history to include if include_history=True (max 365).
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        JSON file download with Content-Disposition header.

    Security:
        Requires admin role via Entra ID authentication.
        Only exports data for the user's own context.
    """
    try:
        # Get user's context
        tenant_id = DEFAULT_TENANT_ID

        # Limit history_days to max 365
        history_days = min(history_days, 365)

        # Get all products with watches in this context
        stmt = (
            select(Product)
            .join(PriceWatch, Product.id == PriceWatch.product_id)
            .where(PriceWatch.tenant_id == tenant_id)
            .where(PriceWatch.is_active.is_(True))
            .distinct()
        )
        result = await session.execute(stmt)
        products = result.scalars().all()

        products_data: list[dict[str, Any]] = []

        for product in products:
            # Get store links
            ps_stmt = (
                select(ProductStore, Store)
                .join(Store, ProductStore.store_id == Store.id)
                .where(ProductStore.product_id == product.id)
            )
            ps_result = await session.execute(ps_stmt)
            ps_rows = ps_result.all()

            store_links = [
                {
                    "store_slug": store.slug,
                    "store_url": ps.store_url,
                    "check_frequency_hours": ps.check_frequency_hours,
                    "check_weekday": ps.check_weekday,
                    "is_active": ps.is_active,
                }
                for ps, store in ps_rows
            ]

            # Get watches for this product in this context
            watch_stmt = select(PriceWatch).where(
                PriceWatch.product_id == product.id,
                PriceWatch.tenant_id == tenant_id,
                PriceWatch.is_active.is_(True),
            )
            watch_result = await session.execute(watch_stmt)
            watches = watch_result.scalars().all()

            watches_data = [
                {
                    "email_address": watch.email_address,
                    "target_price_sek": (
                        float(watch.target_price_sek) if watch.target_price_sek else None
                    ),
                    "alert_on_any_offer": watch.alert_on_any_offer,
                    "price_drop_threshold_percent": watch.price_drop_threshold_percent,
                    "unit_price_target_sek": (
                        float(watch.unit_price_target_sek) if watch.unit_price_target_sek else None
                    ),
                    "unit_price_drop_threshold_percent": watch.unit_price_drop_threshold_percent,
                }
                for watch in watches
            ]

            product_dict: dict[str, Any] = {
                "id": str(product.id),
                "name": product.name,
                "brand": product.brand,
                "category": product.category,
                "unit": product.unit,
                "package_size": product.package_size,
                "package_quantity": (
                    float(product.package_quantity) if product.package_quantity else None
                ),
                "store_links": store_links,
                "watches": watches_data,
            }
            products_data.append(product_dict)

        # Build export data
        export_data: dict[str, Any] = {
            "version": "1.0",
            "exported_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(tenant_id),
            "products": products_data,
            "include_price_history": include_history,
            "price_history": [],
        }

        # Optionally include price history
        if include_history:
            cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=history_days)
            price_history: list[dict[str, Any]] = []

            for product in products:
                # Get price points for all product stores
                ps_history_stmt = select(ProductStore).where(ProductStore.product_id == product.id)
                ps_history_result = await session.execute(ps_history_stmt)
                product_stores = ps_history_result.scalars().all()

                for ps in product_stores:
                    price_stmt = (
                        select(PricePoint, Store)
                        .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                        .join(Store, ProductStore.store_id == Store.id)
                        .where(PricePoint.product_store_id == ps.id)
                        .where(PricePoint.checked_at >= cutoff_date)
                        .order_by(PricePoint.checked_at.desc())
                    )
                    price_result = await session.execute(price_stmt)
                    price_rows = price_result.all()

                    for pp, store in price_rows:
                        price_history.append(
                            {
                                "product_id": str(product.id),
                                "store_slug": store.slug,
                                "checked_at": pp.checked_at.isoformat(),
                                "price_sek": float(pp.price_sek) if pp.price_sek else None,
                                "unit_price_sek": (
                                    float(pp.unit_price_sek) if pp.unit_price_sek else None
                                ),
                                "offer_price_sek": (
                                    float(pp.offer_price_sek) if pp.offer_price_sek else None
                                ),
                                "offer_type": pp.offer_type,
                                "offer_details": pp.offer_details,
                                "in_stock": pp.in_stock,
                            }
                        )

            export_data["price_history"] = price_history

        # Generate filename
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        filename = f"price-tracker-export-{date_str}.json"

        json_content = json.dumps(export_data, indent=2, ensure_ascii=False)

        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to export price tracker data")
        raise HTTPException(status_code=500, detail="Failed to export data") from e


@router.post("/import", )
async def import_data(
    file: UploadFile,
    mode: str = "merge",
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Import price tracker data from JSON.

    Imports products, store links, and watches from a previously exported JSON file.

    Args:
        file: JSON file upload.
        mode: Import mode - "merge" (update existing, add new) or "replace" (delete all first).
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        Summary with counts of created, updated, skipped items and any warnings.

    Security:
        Requires admin role via Entra ID authentication.
        Only imports data to the user's own context.
    """
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode must be 'merge' or 'replace'")

    try:
        # Get user's context
        tenant_id = DEFAULT_TENANT_ID

        # Read and parse JSON
        content = await file.read()
        try:
            data = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

        # Validate version
        version = data.get("version")
        if version != "1.0":
            raise HTTPException(status_code=400, detail=f"Unsupported export version: {version}")

        products_data = data.get("products", [])
        if not isinstance(products_data, list):
            raise HTTPException(status_code=400, detail="Invalid products format")

        # Get all stores for slug lookup
        stores_stmt = select(Store)
        stores_result = await session.execute(stores_stmt)
        stores = stores_result.scalars().all()
        store_by_slug: dict[str, Store] = {store.slug: store for store in stores}

        # If replace mode, delete all user's watches first (products are shared).
        # No commit here — the delete rides the same transaction as the import,
        # so a failed import rolls back to the pre-import state instead of
        # leaving the watches gone.
        if mode == "replace":
            delete_stmt = delete(PriceWatch).where(PriceWatch.tenant_id == tenant_id)
            await session.execute(delete_stmt)

        # Import statistics
        products_created = 0
        products_updated = 0
        products_skipped = 0
        store_links_created = 0
        store_links_skipped = 0
        watches_created = 0
        watches_skipped = 0
        warnings: list[str] = []

        for prod_data in products_data:
            # Validate required fields
            name = prod_data.get("name")
            if not name:
                warnings.append("Skipped product with missing name")
                products_skipped += 1
                continue

            brand = prod_data.get("brand")

            # Find or create product by name+brand
            product_stmt = select(Product).where(Product.name == name)
            if brand:
                product_stmt = product_stmt.where(Product.brand == brand)
            else:
                product_stmt = product_stmt.where(Product.brand.is_(None))

            product_result = await session.execute(product_stmt)
            product = product_result.scalar_one_or_none()

            if product:
                # Update existing product
                product.category = prod_data.get("category") or product.category
                product.unit = prod_data.get("unit") or product.unit
                product.package_size = prod_data.get("package_size") or product.package_size
                if prod_data.get("package_quantity"):
                    product.package_quantity = Decimal(str(prod_data["package_quantity"]))
                products_updated += 1
            else:
                # Create new product
                package_qty = None
                if prod_data.get("package_quantity"):
                    package_qty = Decimal(str(prod_data["package_quantity"]))

                product = Product(
                    tenant_id=tenant_id,
                    name=name,
                    brand=brand,
                    category=prod_data.get("category"),
                    unit=prod_data.get("unit"),
                    package_size=prod_data.get("package_size"),
                    package_quantity=package_qty,
                )
                session.add(product)
                await session.flush()  # Get product ID
                products_created += 1

            # Process store links
            for link_data in prod_data.get("store_links", []):
                store_slug = link_data.get("store_slug")
                store_url = link_data.get("store_url")

                if not store_slug or not store_url:
                    warnings.append("Skipped store link with missing slug or URL")
                    store_links_skipped += 1
                    continue

                store = store_by_slug.get(store_slug)
                if not store:
                    warnings.append(f"Store slug '{store_slug}' not found, skipped link")
                    store_links_skipped += 1
                    continue

                # Check if link already exists
                ps_stmt = select(ProductStore).where(
                    ProductStore.product_id == product.id,
                    ProductStore.store_id == store.id,
                )
                ps_result = await session.execute(ps_stmt)
                existing_ps = ps_result.scalar_one_or_none()

                if not existing_ps:
                    # Create new link
                    ps = ProductStore(
                        product_id=product.id,
                        store_id=store.id,
                        store_url=store_url,
                        check_frequency_hours=link_data.get("check_frequency_hours", 168),
                        check_weekday=link_data.get("check_weekday"),
                        is_active=link_data.get("is_active", True),
                    )
                    session.add(ps)
                    store_links_created += 1
                else:
                    store_links_skipped += 1

            # Process watches
            for watch_data in prod_data.get("watches", []):
                email = watch_data.get("email_address")
                if not email or not EMAIL_PATTERN.match(email):
                    warnings.append(f"Skipped watch with invalid email: {email}")
                    watches_skipped += 1
                    continue

                # Check if watch already exists
                watch_stmt = select(PriceWatch).where(
                    PriceWatch.product_id == product.id,
                    PriceWatch.tenant_id == tenant_id,
                    PriceWatch.email_address == email,
                    PriceWatch.is_active.is_(True),
                )
                watch_result = await session.execute(watch_stmt)
                existing_watch = watch_result.scalar_one_or_none()

                if not existing_watch:
                    target_price = None
                    if watch_data.get("target_price_sek"):
                        target_price = Decimal(str(watch_data["target_price_sek"]))

                    unit_target = None
                    if watch_data.get("unit_price_target_sek"):
                        unit_target = Decimal(str(watch_data["unit_price_target_sek"]))

                    watch = PriceWatch(
                        product_id=product.id,
                        tenant_id=tenant_id,
                        email_address=email,
                        target_price_sek=target_price,
                        alert_on_any_offer=watch_data.get("alert_on_any_offer", False),
                        price_drop_threshold_percent=watch_data.get("price_drop_threshold_percent"),
                        unit_price_target_sek=unit_target,
                        unit_price_drop_threshold_percent=watch_data.get(
                            "unit_price_drop_threshold_percent"
                        ),
                        is_active=True,
                    )
                    session.add(watch)
                    watches_created += 1
                else:
                    watches_skipped += 1

        await session.commit()

        return {
            "message": "Import completed",
            "mode": mode,
            "summary": {
                "products_created": products_created,
                "products_updated": products_updated,
                "products_skipped": products_skipped,
                "store_links_created": store_links_created,
                "store_links_skipped": store_links_skipped,
                "watches_created": watches_created,
                "watches_skipped": watches_skipped,
                "warnings": warnings,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        LOGGER.exception("Failed to import price tracker data")
        raise HTTPException(status_code=500, detail="Failed to import data") from e


@router.get("/scheduler/status")
async def scheduler_status(
    request: Request,
    admin_email: str = Depends(require_auth),
):
    """Get price check scheduler status and statistics.

    Returns:
        Scheduler running state, last summary date, and check stats.

    Security:
        Requires admin role via Entra ID authentication.
    """
    scheduler = request.app.state.scheduler
    if scheduler is None:
        return {"running": False, "last_summary_date": None, "stats": None}
    return scheduler.get_status()  # type: ignore[return-value]


@router.get("/", response_class=HTMLResponse)
async def price_tracker_dashboard(admin_email: str = Depends(require_auth)) -> str:
    """Server-rendered admin dashboard for price tracking.
        HTML dashboard for managing products, deals, and price watches.

    Security:
        Requires admin role via Entra ID authentication.
    """
    template_path = Path(__file__).parent / "templates" / "admin.html"
    parts = template_path.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")

    content = parts[0] if len(parts) > 0 else ""
    extra_css = parts[1] if len(parts) > 1 else ""
    extra_js = parts[2] if len(parts) > 2 else ""

    base_css = _get_admin_nav_css()
    sidebar = _get_admin_sidebar_html()
    header = _get_admin_header_html(admin_email)
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Price Tracker - Admin</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        {base_css}
        {extra_css}
    </style>
</head>
<body>
    <div class="admin-layout">
        {sidebar}
        <main class="admin-main">
            {header}
            <div class="admin-content">
                {content}
            </div>
        </main>
    </div>
    <script>
        {extra_js}
    </script>
</body>
</html>""".format(
        base_css=base_css,
        extra_css=extra_css,
        sidebar=sidebar,
        header=header,
        content=content,
        extra_js=extra_js,
    )

def _get_admin_nav_css() -> str:
    """Return shared CSS for admin navigation."""
    return """
        :root {
            --nav-width: 220px;
            --header-height: 56px;
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --bg: #f8fafc;
            --bg-nav: #1e293b;
            --bg-card: #fff;
            --border: #e2e8f0;
            --text: #1e293b;
            --text-muted: #64748b;
            --text-nav: #94a3b8;
            --text-nav-active: #fff;
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .admin-layout { display: flex; min-height: 100vh; }
        .admin-sidebar { width: var(--nav-width); background: var(--bg-nav); color: var(--text-nav); position: fixed; top: 0; left: 0; bottom: 0; display: flex; flex-direction: column; z-index: 100; }
        .sidebar-header { padding: 16px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .sidebar-logo { font-size: 14px; font-weight: 600; color: #fff; text-decoration: none; display: flex; align-items: center; gap: 8px; }
        .sidebar-logo span { font-size: 18px; }
        .sidebar-nav { flex: 1; overflow-y: auto; padding: 12px 0; }
        .nav-section { padding: 8px 16px 4px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; }
        .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 16px; color: var(--text-nav); text-decoration: none; font-size: 13px; transition: all 0.15s; border-left: 3px solid transparent; }
        .nav-item:hover { background: rgba(255,255,255,0.05); color: #fff; }
        .nav-item.active { background: rgba(37, 99, 235, 0.2); color: var(--text-nav-active); border-left-color: var(--primary); }
        .nav-icon { font-size: 16px; width: 20px; text-align: center; }
        .sidebar-footer { padding: 12px 16px; border-top: 1px solid rgba(255,255,255,0.1); font-size: 11px; color: #64748b; }
        .admin-main { flex: 1; margin-left: var(--nav-width); display: flex; flex-direction: column; min-height: 100vh; }
        .admin-header { height: var(--header-height); background: var(--bg-card); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 24px; position: sticky; top: 0; z-index: 50; }
        .breadcrumbs { display: flex; align-items: center; gap: 8px; font-size: 13px; }
        .breadcrumbs a { color: var(--text-muted); text-decoration: none; }
        .breadcrumbs a:hover { color: var(--primary); }
        .breadcrumbs .separator { color: var(--border); }
        .breadcrumbs .current { color: var(--text); font-weight: 500; }
        .header-actions { display: flex; align-items: center; gap: 12px; }
        .user-menu { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: var(--bg); border-radius: 6px; font-size: 13px; }
        .user-avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--primary); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; }
        .admin-content { flex: 1; padding: 24px; }
        .page-title { font-size: 20px; font-weight: 600; margin-bottom: 20px; }
        .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .card-title { font-size: 14px; font-weight: 600; }
        .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid var(--border); background: var(--bg-card); color: var(--text); transition: all 0.15s; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
        .btn:hover { background: var(--bg); border-color: var(--text-muted); }
        .btn-primary { background: var(--primary); border-color: var(--primary); color: #fff; }
        .btn-primary:hover { background: var(--primary-dark); }
        .btn-sm { padding: 5px 10px; font-size: 12px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: var(--bg); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px; color: var(--text-muted); }
        tr:hover { background: var(--bg); }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge-success { background: #d1fae5; color: #065f46; }
        .badge-warning { background: #fef3c7; color: #92400e; }
        .badge-error { background: #fee2e2; color: #991b1b; }
        .badge-info { background: #dbeafe; color: #1e40af; }
        .badge-muted { background: #e5e7eb; color: #374151; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .status-dot.ok { background: var(--success); }
        .status-dot.warning { background: var(--warning); }
        .status-dot.error { background: var(--error); }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-box { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
        .stat-value { font-size: 24px; font-weight: 600; color: var(--primary); }
        .stat-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .loading { color: var(--text-muted); font-style: italic; text-align: center; padding: 20px; }
        .empty-state { text-align: center; padding: 40px 20px; color: var(--text-muted); }
        .empty-state-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
        .shared-toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 6px; color: white; font-size: 14px; font-weight: 500; z-index: 9999; display: none; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-width: 400px; }
        .shared-toast.success { background: var(--success); display: block; }
        .shared-toast.error { background: var(--error); display: block; }
        .shared-toast.warning { background: var(--warning); display: block; }
        .shared-toast.info { background: var(--primary); display: block; }
    """

def _get_admin_sidebar_html() -> str:
    """Generate sidebar HTML."""
    return """
    <aside class="admin-sidebar">
        <div class="sidebar-header">
            <a href="/" class="sidebar-logo">
                <span>&#128181;</span>
                Price Tracker
            </a>
        </div>
        <nav class="sidebar-nav">
            <a href="/" class="nav-item active">
                <span class="nav-icon">&#128181;</span>
                Price Tracker
            </a>
        </nav>
        <div class="sidebar-footer">
            Price Tracker Admin
        </div>
    </aside>
    """

def _get_admin_header_html(user_email: str) -> str:
    """Generate header HTML with user info."""
    import html as html_module
    safe_email = html_module.escape(user_email)
    user_initial = safe_email[0].upper() if safe_email else "?"
    return f"""
    <header class="admin-header">
        <div class="breadcrumbs">
            <a href="/">Admin</a>
            <span class="separator">/</span>
            <span class="current">Price Tracker</span>
        </div>
        <div class="header-actions">
            <div class="user-menu">
                <div class="user-avatar">{user_initial}</div>
                <span>{safe_email}</span>
            </div>
        </div>
    </header>
    """
