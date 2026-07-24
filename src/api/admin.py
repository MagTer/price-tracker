"""Admin API endpoints for price tracker module."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.auth import require_auth
from api.schemas import (
    DealResponse,
    PricePointResponse,
    PriceWatchCreate,
    PriceWatchUpdate,
    ProductCreate,
    ProductResponse,
    ProductStoreLink,
    ProductStoreUpdate,
    ProductUpdate,
    QuickAddCreate,
    QuickAddPreview,
    StoreResponse,
)
from domain.extractors.jsonld import JsonLdExtractor
from domain.models import PricePoint, PriceWatch, Product, ProductStore, Store, link_store_name
from domain.parser import PriceParser
from domain.pricing import CANONICAL_UNITS, normalize_amount, quantity_mismatch, unit_price_py
from domain.quickadd import (
    PackageGuess,
    derive_unit,
    match_store_by_url,
    parse_package_from_name,
    suggest_existing_products,
    suggest_sibling_links,
    suggest_store_label,
)
from domain.schedule import effective_schedule, is_inherited, next_check_time
from domain.service import PriceTrackerService, perform_price_check
from domain.tenant import DEFAULT_TENANT_ID
from infra.db import async_session_factory
from infra.logbuffer import get_log_buffer
from infra.providers import get_fetcher, get_rate_limiter

LOGGER = logging.getLogger(__name__)

# Interactive fetches (quick-add preview, first check, manual re-check) go through the
# SAME per-store politeness ledger as the scheduler so they cannot burst a store's WAF
# (see infra.rate_limiter). A short spacing — a mashed "förhandsgranska" button or the
# scheduler landing on the same store a second earlier no longer fires back-to-back —
# capped so a human never waits on a background reservation more than QUICKADD_MAX_WAIT.
QUICKADD_RATE_LIMIT_DELAY = float(os.getenv("QUICKADD_RATE_LIMIT_DELAY", "5"))
QUICKADD_MAX_WAIT = float(os.getenv("QUICKADD_MAX_WAIT", "10"))

_CENT = Decimal("0.01")


def _read_app_version() -> str:
    """The running app's version, for the sidebar footer.

    Installed metadata first (the Docker image); pyproject.toml as the dev fallback, so
    the footer matches the tag-source-of-truth rule ("the version in pyproject.toml IS
    the tag") in every environment. Empty string — footer shows no version — only if
    both fail.
    """
    try:
        from importlib.metadata import version

        return version("price-tracker")
    except Exception:  # noqa: S110 - PackageNotFoundError or broken metadata
        pass
    try:
        import tomllib

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        return str(tomllib.loads(pyproject.read_text())["project"]["version"])
    except Exception:
        return ""


_APP_VERSION = _read_app_version()


def _effective_price(price_point: PricePoint | None) -> Decimal | None:
    """The price actually paid: the offer when there is one, else the regular price."""
    if price_point is None:
        return None
    if price_point.offer_price_sek is not None:
        return price_point.offer_price_sek
    return price_point.price_sek


def _computed_unit_price(price: Decimal | None, quantity: Decimal | None) -> float | None:
    """kr/unit for a response — the single definition (D-03), rounded at this boundary.

    None when the link has no amount yet (D-02): the row says "needs amount", it does not lie
    with a zero.

    The quantity always comes from the LINK that is already in scope at the call site. The
    ORM hybrid's Python side reads PricePoint.product_store, which lazy-loads in a non-greenlet
    context and raises MissingGreenlet at request time on any price point loaded without a
    joinedload — and a mocked test would never see it. So: never the hybrid here, always this.
    """
    value = unit_price_py(price, quantity)
    if value is None:
        return None
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _as_float(value: Decimal | None) -> float | None:
    """Decimal -> float for a response dict, preserving None."""
    return float(value) if value is not None else None


def _link_payload(
    ps: ProductStore, store: Store, latest_price: PricePoint | None
) -> dict[str, str | int | None | float | bool]:
    """One link's wire shape — the ONE builder behind every `stores` array we emit.

    It was written twice (the list route and the detail route), byte for byte. Two copies of a
    wire contract drift, and this one already has form: the price-history route's own duplicate
    query is what silently dropped the package fields (see CLAUDE.md, Gotcha 4).
    """
    return {
        "product_store_id": str(ps.id),
        "store_id": str(ps.store_id),
        # The LINK's display name (label wins over chain) — two ICA-butik links must not
        # both render as "ICA".
        "store_name": link_store_name(ps, store),
        # The raw label separately, so edit dialogs can tell label from chain fallback.
        "store_label": ps.store_label,
        "store_slug": store.slug,
        "store_url": ps.store_url,
        # Raw override fields plus the resolved schedule — same shape as the links route.
        "check_frequency_hours": ps.check_frequency_hours,
        "check_weekdays": ps.check_weekdays,
        "schedule_inherited": is_inherited(ps),
        "effective_check_weekdays": effective_schedule(ps, store)[0],
        "effective_check_frequency_hours": effective_schedule(ps, store)[1],
        "store_schedule": {
            "weekdays": store.check_weekdays or [],
            "frequency_hours": store.check_frequency_hours,
        },
        "last_checked_at": ps.last_checked_at.isoformat() if ps.last_checked_at else None,
        # The package is the LINK's, not the product's.
        "package_size": ps.package_size,
        "package_quantity": _as_float(ps.package_quantity),
        "scraped_package_quantity": _as_float(ps.scraped_package_quantity),
        "price_sek": (
            float(latest_price.price_sek) if latest_price and latest_price.price_sek else None
        ),
        "offer_price_sek": (_as_float(latest_price.offer_price_sek) if latest_price else None),
        # COMPUTED from the link's own quantity (D-03) — the comparable number.
        "unit_price_sek": _computed_unit_price(_effective_price(latest_price), ps.package_quantity),
        # What the STORE printed (D-05) — display only, never sorted on.
        "store_unit_price_sek": (
            _as_float(latest_price.store_unit_price_sek) if latest_price else None
        ),
        "in_stock": latest_price.in_stock if latest_price else None,
        # D-02's visible flag: a link may be saved without an amount, but it is never
        # SILENTLY blank.
        "needs_amount": ps.package_quantity is None,
        # D-09's derived, self-clearing flag — never persisted as a boolean.
        "quantity_mismatch": quantity_mismatch(ps),
    }


def _sorted_links(
    rows: list[tuple[ProductStore, Store, PricePoint | None]],
) -> list[dict[str, str | int | None | float | bool]]:
    """Links, cheapest kr/unit first, links with no amount last — the same order the domain
    service already emits (`unit_price_expr(...).asc().nulls_last()`).

    Both routes used to return whatever order Postgres happened to hand back: no ORDER BY at
    all. That is not "unsorted", it is *arbitrary and unstable* — it can change under the same
    data — and the frontend was reading `stores[0]` as if it meant something.

    Sorted on the UNROUNDED Decimal (pricing.unit_price_py), not on the rounded float in the
    payload: round first and two genuinely different prices tie, then fall through to the
    tiebreak and swap places for no reason. Ties break on store name, then link id, so equal
    prices hold still between renders.
    """
    return [
        _link_payload(ps, store, pp)
        for ps, store, pp in sorted(
            rows,
            key=lambda row: (
                unit_price_py(_effective_price(row[2]), row[0].package_quantity) is None,
                unit_price_py(_effective_price(row[2]), row[0].package_quantity) or Decimal(0),
                row[1].name,
                str(row[0].id),
            ),
        )
    ]


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
        raise HTTPException(status_code=400, detail="Ogiltigt format på tenant_id") from e
    if tenant_uuid != DEFAULT_TENANT_ID:
        raise HTTPException(
            status_code=403,
            detail="Åtkomst nekad: du kan bara agera i din egen kontext",
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


@router.get("/stores", response_model=list[StoreResponse])
async def list_stores(session: AsyncSession = Depends(get_db)) -> list[StoreResponse]:
    """List all configured stores.
        List of store information including slug, type, and status.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
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
        session: Database session.

    Returns:
        List of products with linked stores.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
        Users can only query their own tenant_id.
    """
    try:
        # Security check: if tenant_id provided, verify user has access to it
        if tenant_id:
            try:
                tenant_uuid = uuid.UUID(tenant_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Ogiltigt format på tenant_id") from e

            # Verify user can only query their own context
            if tenant_uuid != DEFAULT_TENANT_ID:
                raise HTTPException(
                    status_code=403,
                    detail="Åtkomst nekad: du kan bara se produkter i din egen kontext",
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
                raise HTTPException(status_code=400, detail="Ogiltigt format på store_id") from e

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
                    select(PricePoint, rn).where(PricePoint.product_store_id.in_(ps_ids)).subquery()
                )
                latest_pp = aliased(PricePoint, latest_subq)
                latest_result = await session.execute(
                    select(latest_pp).where(latest_subq.c.rn == 1)
                )
                for pp in latest_result.scalars().all():
                    latest_by_ps[pp.product_store_id] = pp

        product_responses: list[ProductResponse] = []
        for product in products:
            stores_data = _sorted_links(
                [
                    (ps, store, latest_by_ps.get(ps.id))
                    for ps, store in ps_by_product.get(product.id, [])
                ]
            )

            product_responses.append(
                ProductResponse(
                    id=str(product.id),
                    name=product.name,
                    brand=product.brand,
                    category=product.category,
                    unit=product.unit,
                    stores=stores_data,
                )
            )

        return product_responses
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list products")
        raise HTTPException(status_code=500, detail="Failed to list products") from e


@router.post(
    "/products",
    status_code=201,
)
async def create_product(
    data: ProductCreate,
    admin_email: str = Depends(require_auth),
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Create a new product to track.

    A product is the abstract good (name, brand, category, and its canonical comparison
    `unit`). Its package listings — the 24-pack, the 500 ml bottle — live on the STORE LINKS,
    so the same good may be tracked at several sizes in the same store.

    Products are scoped to the authenticated user's context for multi-tenancy.

    Args:
        data: Product creation data.
        session: Database session.
        service: Price tracker service.

    Returns:
        Dictionary with product_id and success message.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        tenant_uuid = require_default_tenant(data.tenant_id)

        product = await service.create_product(
            tenant_id=tenant_uuid,
            name=data.name,
            brand=data.brand,
            category=data.category,
            unit=data.unit,
        )
        return {"product_id": str(product.id), "message": "Product created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to create product")
        raise HTTPException(status_code=500, detail="Failed to create product") from e


@router.post("/quick-add/preview")
async def quick_add_preview(
    data: QuickAddPreview,
    session: AsyncSession = Depends(get_db),
    admin_email: str = Depends(require_auth),
) -> dict[str, Any]:
    """Everything quick-add can infer from one pasted URL — a suggestion bundle, no writes.

    Extraction ladder for identity (name/brand): JSON-LD Product node first (exact and free
    on the four JSON-LD stores), LLM metadata fallback only when the page has no usable
    node. Store matching and package/unit derivation are pure coded logic (domain.quickadd).

    Every field in the response lands in an EDITABLE preview form. The human confirm step
    is deliberate: it is the carousel guard (JSON-LD's name-overlap sanity check cannot run
    without a tracked name) and the place where "new product" vs "new link on an existing
    product" gets decided — the choice that keeps quick-add from rebuilding the
    one-product-per-pack-size world 04.1 abolished.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    url = (data.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Ange en fullständig produkt-URL (https://…)")

    try:
        stores_stmt = select(Store).where(Store.is_active.is_(True))
        stores = list((await session.execute(stores_stmt)).scalars().all())
        store = match_store_by_url(url, stores)
        if store is None:
            names = ", ".join(sorted(s.name for s in stores))
            raise HTTPException(
                status_code=422,
                detail=f"URL:en matchar ingen av butikerna ({names})",
            )

        # store_url is the link's natural key (globally unique) — an already-tracked URL
        # short-circuits BEFORE the fetch: no page load, no LLM call, just the answer.
        dup_stmt = (
            select(Product.id, Product.name)
            .join(ProductStore, ProductStore.product_id == Product.id)
            .where(ProductStore.store_url == url)
        )
        dup = (await session.execute(dup_stmt)).first()
        if dup:
            return {
                "url": url,
                "store": {"id": str(store.id), "name": store.name, "slug": store.slug},
                "already_tracked": {"product_id": str(dup[0]), "product_name": dup[1]},
            }

        await get_rate_limiter().acquire(
            store.id, QUICKADD_RATE_LIMIT_DELAY, max_wait=QUICKADD_MAX_WAIT
        )
        fetch_result = await get_fetcher().fetch(url)
        if not fetch_result.get("ok"):
            raise HTTPException(
                status_code=502,
                detail=f"Kunde inte hämta sidan: {fetch_result.get('error')}",
            )

        html = fetch_result.get("html") or ""
        extractor = JsonLdExtractor()
        meta = extractor.extract_product_metadata(html) if html else None
        # No product_name yet, so the name-overlap sanity check is skipped — acceptable
        # because this price is preview display only; the recorded first price comes from
        # perform_price_check after confirm.
        price_result = extractor.extract_from_html(html) if html else None

        name = meta.get("name") if meta else None
        brand = meta.get("brand") if meta else None
        category: str | None = None
        price = price_result.price_sek if price_result else None
        in_stock = price_result.in_stock if price_result else None
        source = "jsonld" if name else None
        guess = parse_package_from_name(name)

        if name is None:
            # Pass the raw HTML too: for a JS SPA the identity lives in <title>/<meta>/
            # JSON-LD that the stripped text drops, so text alone yields an all-null result.
            llm_meta = await PriceParser().extract_product_metadata(
                fetch_result.get("text", ""), store.slug, html_content=html
            )
            if llm_meta is not None:
                name = llm_meta.name
                brand = brand or llm_meta.brand
                category = llm_meta.category
                price = price if price is not None else llm_meta.price_sek
                source = "llm"
                guess = parse_package_from_name(name)
                if guess.amount is None and llm_meta.package_amount is not None:
                    # The LLM read the package off the PAGE — better evidence than the title.
                    guess = PackageGuess(
                        amount=llm_meta.package_amount,
                        entry_unit=llm_meta.package_unit,
                        pack_size=llm_meta.pack_size,
                        label=(f"{llm_meta.package_amount} {llm_meta.package_unit or ''}".strip()),
                    )

        suggested_unit = derive_unit(guess.entry_unit, guess.pack_size)
        package_quantity = normalize_amount(guess.amount, guess.entry_unit, suggested_unit)

        products = list((await session.execute(select(Product))).scalars().all())
        candidates = [
            {"id": str(p.id), "name": p.name, "brand": p.brand, "unit": p.unit} for p in products
        ]
        suggestions = suggest_existing_products(name, candidates)

        # Sister-butik candidates (same product page, /stores/<id>/ swapped — verified
        # live 2026-07-21) — offered as a pre-checked choice in the confirm step; each
        # accepted sibling is verified by its own first fetch before it is kept.
        sibling_payload = []
        for sibling in suggest_sibling_links(url, store.name):
            tracked = (
                await session.execute(
                    select(ProductStore.id).where(ProductStore.store_url == sibling.url)
                )
            ).first() is not None
            sibling_payload.append(
                {
                    "url": sibling.url,
                    "store_label": sibling.store_label,
                    "already_tracked": tracked,
                }
            )

        return {
            "url": url,
            "store": {"id": str(store.id), "name": store.name, "slug": store.slug},
            "already_tracked": None,
            # Per-butik label from the URL's /stores/<id>/ segment (ICA prices per butik).
            # None for nationally-priced chains — the chain name suffices there.
            "suggested_store_label": suggest_store_label(url, store.name),
            # The store's own schedule, for the confirm step's read-only info line — the
            # link INHERITS it (quick-add never sets a per-link schedule; the override
            # lives in the link-edit dialog for the rare case that earns one).
            "store_schedule": {
                "weekdays": store.check_weekdays or [],
                "frequency_hours": store.check_frequency_hours,
            },
            "name": name,
            "brand": brand,
            "category": category,
            "suggested_unit": suggested_unit,
            "package_size": guess.label,
            "package_quantity": _as_float(package_quantity),
            "price_sek": _as_float(price),
            "in_stock": in_stock,
            "source": source,
            "existing_products": suggestions,
            "sibling_links": sibling_payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Quick-add preview failed for %s", sanitize_log(url))
        raise HTTPException(status_code=500, detail="Quick-add preview failed") from e


@router.post("/quick-add", status_code=201)
async def quick_add(
    data: QuickAddCreate,
    session: AsyncSession = Depends(get_db),
    service: PriceTrackerService = Depends(get_price_tracker_service),
    admin_email: str = Depends(require_auth),
) -> dict[str, Any]:
    """Confirm a quick-add: product (new or existing) + link + first check, one call.

    Composes the SAME service methods as the manual flow (create_product,
    link_product_store) and the SAME check flow (perform_price_check) — quick-add adds no
    second write path. The first check also drives the D-07 package autofill, so a link
    confirmed without a quantity usually comes back from this call with one.

    Failure semantics: creation is the task, the first check is best-effort. A fetch or
    extraction failure after a successful create returns 201 with ``check.success=false``
    — the scheduler retries later; the product and link exist either way.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    # Same guards as the manual link endpoint — quick-add must not be a validation bypass.
    # (No schedule guards: quick-add creates the link INHERITING the store's schedule.)
    if data.package_quantity is not None and data.package_quantity <= 0:
        raise HTTPException(status_code=400, detail="package_quantity måste vara positiv")
    if data.unit is not None and data.unit not in CANONICAL_UNITS:
        raise HTTPException(
            status_code=400,
            detail=f"unit måste vara en av: {', '.join(CANONICAL_UNITS)}",
        )
    if data.product_id is None and not (data.name or "").strip():
        raise HTTPException(status_code=400, detail="Produktnamn krävs för en ny produkt")
    try:
        uuid.UUID(data.store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på store_id") from e

    url = (data.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Ange en fullständig produkt-URL (https://…)")

    try:
        # Duplicate check BEFORE creating the product, so a duplicate URL cannot leave an
        # orphaned just-created product behind (the IntegrityError arm below is the
        # race-window backstop, not the primary guard).
        dup_stmt = select(ProductStore.id).where(ProductStore.store_url == url)
        if (await session.execute(dup_stmt)).first() is not None:
            raise HTTPException(
                status_code=409,
                detail="Den här butiks-URL:en bevakas redan — varje länk har sin egen URL",
            )

        created_product = False
        if data.product_id is not None:
            try:
                product_uuid = uuid.UUID(data.product_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Ogiltigt format på product_id") from e
            existing = (
                await session.execute(select(Product).where(Product.id == product_uuid))
            ).scalar_one_or_none()
            if existing is None:
                raise HTTPException(status_code=404, detail="Produkten hittades inte")
            product_id = str(existing.id)
        else:
            product = await service.create_product(
                tenant_id=DEFAULT_TENANT_ID,
                name=data.name.strip(),
                brand=(data.brand or "").strip() or None,
                category=(data.category or "").strip() or None,
                unit=data.unit,
            )
            product_id = str(product.id)
            created_product = True

        package_qty = Decimal(str(data.package_quantity)) if data.package_quantity else None
        try:
            product_store = await service.link_product_store(
                product_id=product_id,
                store_id=data.store_id,
                store_url=url,
                package_size=data.package_size,
                package_quantity=package_qty,
                store_label=(data.store_label or "").strip() or None,
            )
        except IntegrityError as e:
            if created_product:
                await service.delete_product(product_id)
            raise HTTPException(
                status_code=409,
                detail="Den här butiks-URL:en bevakas redan — varje länk har sin egen URL",
            ) from e
        except Exception:
            if created_product:
                await service.delete_product(product_id)
            raise

        check: dict[str, Any] | None = None
        if data.run_first_check:
            check = await _run_first_check(session, product_store.id)

        sibling_links: list[dict[str, Any]] = []
        if data.add_siblings:
            sibling_links = await _add_sibling_links(
                session=session,
                service=service,
                product_id=product_id,
                data=data,
                url=url,
            )

        return {
            "product_id": product_id,
            "product_store_id": str(product_store.id),
            "created_product": created_product,
            "check": check,
            "sibling_links": sibling_links,
            "message": "Produkt och länk skapade",
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Quick-add failed for %s", sanitize_log(url))
        raise HTTPException(status_code=500, detail="Quick-add failed") from e


async def _add_sibling_links(
    *,
    session: AsyncSession,
    service: PriceTrackerService,
    product_id: str,
    data: QuickAddCreate,
    url: str,
) -> list[dict[str, Any]]:
    """Create the sister-butik links for a just-confirmed quick-add (best-effort).

    A sibling is a CANDIDATE, not a fact: the URL swap almost always holds (chain-wide
    product ids, verified live), but the assortment may differ per butik. Siblings are
    created optimistically and left for the scheduler to check like any other link — they
    are NOT fetched here. Sister butiker share one store_id (one host + WAF), so verifying
    each inline was the very same-host burst that tripped the rate limit; and now that the
    fetcher reports a WAF block as a failed fetch, an inline check could not tell "this
    butik lacks the product" from "we are momentarily blocked", so it would wrongly delete
    valid siblings. A truly absent sibling simply keeps failing its scheduled checks.

    Never raises: the primary link is the task, siblings report their own outcome —
    ``created`` / ``already_tracked`` / ``error`` per entry.
    """
    store_name_row = (
        await session.execute(select(Store.name).where(Store.id == uuid.UUID(data.store_id)))
    ).first()
    store_name = store_name_row[0] if store_name_row else ""
    package_qty = Decimal(str(data.package_quantity)) if data.package_quantity else None

    results: list[dict[str, Any]] = []
    for sibling in suggest_sibling_links(url, store_name):
        entry: dict[str, Any] = {"url": sibling.url, "store_label": sibling.store_label}
        try:
            dup = (
                await session.execute(
                    select(ProductStore.id).where(ProductStore.store_url == sibling.url)
                )
            ).first()
            if dup is not None:
                entry["status"] = "already_tracked"
                results.append(entry)
                continue

            # Same product, same listing — the sibling inherits the primary's package
            # and cadence; only URL and butik label differ.
            product_store = await service.link_product_store(
                product_id=product_id,
                store_id=data.store_id,
                store_url=sibling.url,
                package_size=data.package_size,
                package_quantity=package_qty,
                store_label=sibling.store_label,
            )
            entry["product_store_id"] = str(product_store.id)
            entry["status"] = "created"
        except IntegrityError:
            entry["status"] = "already_tracked"
        except Exception:
            LOGGER.exception("Sibling link creation failed for %s", sanitize_log(sibling.url))
            entry["status"] = "error"
        results.append(entry)
    return results


async def _remove_link(session: AsyncSession, product_store_id: uuid.UUID) -> None:
    """Delete a just-created link again (failed sibling verification)."""
    link = (
        await session.execute(select(ProductStore).where(ProductStore.id == product_store_id))
    ).scalar_one_or_none()
    if link is not None:
        await session.delete(link)
        await session.commit()


async def _run_first_check(session: AsyncSession, product_store_id: uuid.UUID) -> dict[str, Any]:
    """Best-effort first price check for a just-created link.

    Never raises: quick-add's creation already succeeded, so a failed check reports
    ``success=false`` and leaves the link for the scheduler — the same eventual path a
    manually created link takes.
    """
    try:
        stmt = (
            select(ProductStore, Store, Product)
            .join(Store, ProductStore.store_id == Store.id)
            .join(Product, ProductStore.product_id == Product.id)
            .where(ProductStore.id == product_store_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return {"success": False, "reason": "link_not_found"}

        product_store, store, product = row
        await get_rate_limiter().acquire(
            store.id, QUICKADD_RATE_LIMIT_DELAY, max_wait=QUICKADD_MAX_WAIT
        )
        outcome = await perform_price_check(
            product_store=product_store,
            product=product,
            store=store,
            session=session,
            fetcher=get_fetcher(),
            parser=PriceParser(),
        )
        if not outcome.success:
            return {"success": False, "reason": outcome.failure_reason}

        price_point = outcome.price_point
        # Response built BEFORE commit — commit expires the instances (MissingGreenlet).
        result = {
            "success": True,
            "price_sek": float(price_point.price_sek),
            "unit_price_sek": _computed_unit_price(
                _effective_price(price_point), product_store.package_quantity
            ),
            "package_quantity": _as_float(product_store.package_quantity),
            "offer_price_sek": _as_float(price_point.offer_price_sek),
            "in_stock": price_point.in_stock,
        }
        # perform_price_check does not commit — the caller owns the transaction.
        await session.commit()
        return result
    except Exception:
        LOGGER.exception("First check failed for new link %s", product_store_id)
        return {"success": False, "reason": "error"}


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
)
async def get_product(
    product_id: str,
    session: AsyncSession = Depends(get_db),
) -> ProductResponse:
    """Get a product by ID.

    Args:
        product_id: Product UUID.
        session: Database session.

    Returns:
        Product data with linked stores.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_id") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Produkten hittades inte")

        # Get linked stores
        ps_stmt = (
            select(ProductStore, Store)
            .join(Store, ProductStore.store_id == Store.id)
            .where(ProductStore.product_id == product.id)
        )
        ps_result = await session.execute(ps_stmt)
        ps_rows = ps_result.all()

        rows: list[tuple[ProductStore, Store, PricePoint | None]] = []
        for ps, store in ps_rows:
            # Get latest price point for this product-store
            price_stmt = (
                select(PricePoint)
                .where(PricePoint.product_store_id == ps.id)
                .order_by(PricePoint.checked_at.desc())
                .limit(1)
            )
            price_result = await session.execute(price_stmt)
            rows.append((ps, store, price_result.scalar_one_or_none()))

        stores_data = _sorted_links(rows)

        return ProductResponse(
            id=str(product.id),
            name=product.name,
            brand=product.brand,
            category=product.category,
            unit=product.unit,
            stores=stores_data,
        )
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to get product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to retrieve product") from e


@router.get("/products/{product_id}/links")
async def get_product_links(
    product_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> list[dict[str, Any]]:
    """The product page's data source (D-12): one row per LINK, cheapest per unit first.

    This is the view that answers the phase's whole question — "which pack size at which store
    is cheapest per roll". The ordering is the SERVICE's (computed kr/unit ascending, links
    still needing an amount last); this endpoint renders it and must never re-sort, because the
    only comparable unit price is the computed one and the domain owns that definition.

    Each row carries the package label and quantity, the page's own last reading of that
    quantity, both unit prices in separate keys (computed vs store-printed), and the two
    derived flags `needs_amount` (D-02) and `quantity_mismatch` (D-09).

    Args:
        product_id: Product UUID.
        service: Price tracker service.

    Returns:
        Link rows, cheapest computed unit price first.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_id") from e

    try:
        return await service.get_links_for_product(product_id)
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to get links for product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to retrieve product links") from e


@router.put("/products/{product_id}")
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
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_id") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Produkten hittades inte")

        # Update only provided fields. A blank name is a delete-by-accident, not an edit —
        # brand/category clear on empty string, the name never does.
        if data.name is not None:
            if not data.name.strip():
                raise HTTPException(status_code=400, detail="Produktnamnet kan inte vara tomt")
            product.name = data.name.strip()
        if data.brand is not None:
            product.brand = data.brand if data.brand else None
        if data.category is not None:
            product.category = data.category if data.category else None
        # `unit` is NOT updatable (locked in the schema): every link's package_quantity and
        # the whole kr/unit history are expressed in it — changing unit = delete + recreate.
        # Package data is edited on the LINK — see PUT /product-stores/{id}/packaging.

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
)
async def link_product_to_store(
    product_id: str,
    data: ProductStoreLink,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Link a product to a store with URL — the concrete package listing at one store page.

    A product may hold SEVERAL links at one store (a 24-pack and an 8-pack): the link, not the
    product, owns the packaging. The store URL is the link's natural key and is globally unique.

    Args:
        product_id: Product UUID.
        data: Store link data, including the link's package_size / package_quantity.
        service: Price tracker service.

    Returns:
        Dictionary with product_store_id and success message.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    # Schedule fields are an OVERRIDE — None inherits the store's schedule. Validate
    # only what was actually provided (3-to-10-day interval, weekdays 0..6).
    if data.check_frequency_hours is not None and not (72 <= data.check_frequency_hours <= 240):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours måste vara mellan 72 och 240 (inklusive)",
        )
    if data.check_weekdays is not None and any(not (0 <= d <= 6) for d in data.check_weekdays):
        raise HTTPException(
            status_code=400,
            detail="check_weekdays måste innehålla dagar mellan 0 (måndag) och 6 (söndag)",
        )
    # Validate package_quantity if provided. This check MOVED here from create_product when
    # the package data moved onto the link; it was not dropped in the move. A zero quantity
    # would otherwise reach the unit-price divisor.
    if data.package_quantity is not None and data.package_quantity <= 0:
        raise HTTPException(status_code=400, detail="package_quantity måste vara positiv")

    package_qty = Decimal(str(data.package_quantity)) if data.package_quantity else None

    try:
        product_store = await service.link_product_store(
            product_id=product_id,
            store_id=data.store_id,
            store_url=data.store_url,
            check_frequency_hours=data.check_frequency_hours,
            check_weekdays=data.check_weekdays,
            package_size=data.package_size,
            package_quantity=package_qty,
            store_label=(data.store_label or "").strip() or None,
        )
        return {
            "product_store_id": str(product_store.id),
            "message": "Product linked to store successfully",
        }
    except HTTPException:
        raise
    except IntegrityError as e:
        # store_url is globally unique, so pasting an already-tracked URL is a normal user
        # action, not a fault. Without this arm it falls into the blanket handler below and
        # comes back as a 500 carrying a driver message.
        LOGGER.warning(
            "Duplicate store_url on link attempt for product %s: %s",
            sanitize_log(product_id),
            sanitize_log(data.store_url),
        )
        raise HTTPException(
            status_code=409,
            detail="Den här butiks-URL:en bevakas redan — varje länk har sin egen URL",
        ) from e
    except Exception as e:
        LOGGER.exception(
            "Failed to link product %s to store %s",
            sanitize_log(product_id),
            sanitize_log(data.store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to link product to store") from e


@router.put(
    "/product-stores/{product_store_id}/frequency",
)
async def update_check_frequency(
    product_store_id: str,
    request: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Update a link's schedule OVERRIDE — or clear it back to the store's schedule.

    Both fields null (or absent) means "follow the store" — the normal state. Setting
    either field takes over wholesale (domain.schedule.effective_schedule). Every save
    reschedules next_check_at from the resolved schedule, through the SAME domain
    function the scheduler uses — this endpoint used to carry its own copy of the
    weekday arithmetic, which is the Gotcha-4 drift pattern.

    Keyed on the LINK's own id, never on the (product_id, store_id) pair: a product may hold
    several links at one store, so the pair is no longer single-valued and resolving on it
    raises MultipleResultsFound.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_store_id") from e

    check_frequency_hours = request.get("check_frequency_hours")
    check_weekdays = request.get("check_weekdays")  # list of 0=Monday..6=Sunday, or null

    if check_frequency_hours is not None and not (
        isinstance(check_frequency_hours, int) and 72 <= check_frequency_hours <= 240
    ):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours måste vara mellan 72 och 240 (inklusive)",
        )
    if check_weekdays is not None and (
        not isinstance(check_weekdays, list)
        or any(not isinstance(d, int) or not (0 <= d <= 6) for d in check_weekdays)
    ):
        raise HTTPException(
            status_code=400,
            detail="check_weekdays måste innehålla dagar mellan 0 (måndag) och 6 (söndag)",
        )
    # An explicit empty list is not a meaningful override — interval mode without an
    # interval is just the store schedule said backwards. Normalize to inherit.
    if check_weekdays == [] and check_frequency_hours is None:
        check_weekdays = None

    try:
        stmt = (
            select(ProductStore)
            .options(joinedload(ProductStore.store))
            .where(ProductStore.id == ps_uuid)
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Produkt–butikslänken hittades inte")

        product_store.check_frequency_hours = check_frequency_hours
        product_store.check_weekdays = check_weekdays

        now_utc = datetime.now(UTC).replace(tzinfo=None)
        weekdays, frequency = effective_schedule(product_store, product_store.store)
        product_store.next_check_at = next_check_time(weekdays, frequency, now_utc)

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
            "Failed to update frequency for link %s",
            sanitize_log(product_store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to update frequency") from e


@router.put(
    "/product-stores/{product_store_id}/packaging",
)
async def update_link_packaging(
    product_store_id: str,
    data: ProductStoreUpdate,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | float | None]:
    """Edit a link's packaging — its amount and its human-readable label (D-11).

    Deliberately cannot change store_url: the URL is the link's identity, and re-pointing it at
    a different page would rewrite the meaning of the link's entire price history.

    Args:
        product_store_id: ProductStore UUID.
        data: package_size and/or package_quantity.
        session: Database session.

    Returns:
        The updated packaging values.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_store_id") from e

    # The same >0 check the link-create path enforces — relocated from create_product when the
    # package data moved onto the link, not dropped in the move.
    if data.package_quantity is not None and data.package_quantity <= 0:
        raise HTTPException(status_code=400, detail="package_quantity måste vara positiv")

    try:
        stmt = select(ProductStore).where(ProductStore.id == ps_uuid)
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Produkt–butikslänken hittades inte")

        if data.package_size is not None:
            product_store.package_size = data.package_size if data.package_size else None
        if data.package_quantity is not None:
            product_store.package_quantity = Decimal(str(data.package_quantity))
        # Same omitted-vs-empty semantics as package_size: an omitted field is untouched,
        # an empty string clears the label back to the chain-name fallback.
        if data.store_label is not None:
            product_store.store_label = data.store_label.strip() or None

        await session.commit()
        await session.refresh(product_store)

        return {
            "message": "Packaging updated",
            "package_size": product_store.package_size,
            "package_quantity": (
                float(product_store.package_quantity)
                if product_store.package_quantity is not None
                else None
            ),
            "store_label": product_store.store_label,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(
            "Failed to update packaging for link %s",
            sanitize_log(product_store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to update packaging") from e


@router.delete(
    "/product-stores/{product_store_id}",
)
async def unlink_product_from_store(
    product_store_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Remove a product-store link, addressed by its own id.

    Args:
        product_store_id: ProductStore UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_store_id") from e

    try:
        stmt = select(ProductStore).where(ProductStore.id == ps_uuid)
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Produkt–butikslänken hittades inte")

        await session.delete(product_store)
        await session.commit()

        return {"message": "Product unlinked from store successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(
            "Failed to unlink link %s",
            sanitize_log(product_store_id),
        )
        raise HTTPException(status_code=500, detail="Failed to unlink product from store") from e


@router.get(
    "/products/{product_id}/prices",
    response_model=list[PricePointResponse],
)
async def get_price_history(
    product_id: str,
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
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_id") from e

    try:
        from datetime import timedelta

        cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

        # ProductStore was already joined; adding it to the SELECT tuple puts the link's
        # package_quantity in the row, so the unit price is computed from the link with no
        # extra query and without touching the ORM hybrid's Python side (MissingGreenlet).
        stmt = (
            select(PricePoint, Store, ProductStore)
            .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
            .join(Store, ProductStore.store_id == Store.id)
            .where(ProductStore.product_id == product_uuid)
            .where(PricePoint.checked_at >= cutoff_date)
            .order_by(PricePoint.checked_at.desc())
        )

        result = await session.execute(stmt)
        rows = result.all()

        # Computing on read (D-04) is why correcting a link's quantity from 24 to 12
        # retroactively fixes every historical unit price for free: no stale snapshot of a
        # derived number exists to go wrong.
        return [
            PricePointResponse(
                checked_at=price_point.checked_at.isoformat(),
                product_store_id=str(product_store.id),
                store_name=link_store_name(product_store, store),
                store_slug=store.slug,
                package_size=product_store.package_size,
                package_quantity=_as_float(product_store.package_quantity),
                price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                unit_price_sek=_computed_unit_price(
                    _effective_price(price_point), product_store.package_quantity
                ),
                store_unit_price_sek=_as_float(price_point.store_unit_price_sek),
                offer_price_sek=(
                    float(price_point.offer_price_sek) if price_point.offer_price_sek else None
                ),
                offer_type=price_point.offer_type,
                offer_details=price_point.offer_details,
                in_stock=price_point.in_stock,
            )
            for price_point, store, product_store in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to get price history for product %s", sanitize_log(product_id))
        raise HTTPException(status_code=500, detail="Failed to retrieve price history") from e


@router.post("/check/{product_store_id}")
async def trigger_price_check(
    product_store_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | float | bool | None]:
    """Manually trigger a price check for a product-store combination.

    Delegates to domain.service.perform_price_check — the single fetch → extract →
    enrich → apply-scrape → record flow the scheduler also uses — then commits the
    request session and renders the HTTP contract.

    Args:
        product_store_id: ProductStore UUID.
        session: Database session.

    Returns:
        Dictionary with extracted price data.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på product_store_id") from e

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
            raise HTTPException(status_code=404, detail="Produkt–butikslänken hittades inte")

        product_store, store, product = row

        await get_rate_limiter().acquire(
            store.id, QUICKADD_RATE_LIMIT_DELAY, max_wait=QUICKADD_MAX_WAIT
        )
        outcome = await perform_price_check(
            product_store=product_store,
            product=product,
            store=store,
            session=session,
            fetcher=get_fetcher(),
            parser=PriceParser(),
        )

        if outcome.failure_reason == "fetch_failed":
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch page: {outcome.fetch_error}"
            )

        if outcome.failure_reason == "no_price":
            return {
                "message": "Price extraction failed - no price found",
                "confidence": outcome.extraction.confidence,
                "price_sek": None,
                "offer_price_sek": None,
            }

        if outcome.mismatch:
            LOGGER.warning(
                "Package quantity mismatch on link %s: %s",
                sanitize_log(product_store_id),
                outcome.mismatch,
            )

        price_point = outcome.price_point

        # Build the response BEFORE commit: commit expires the instances, and touching
        # an expired attribute afterwards lazy-loads outside a greenlet (MissingGreenlet).
        response: dict[str, str | float | bool | None] = {
            "message": "Price check completed successfully",
            "price_sek": float(price_point.price_sek),
            # COMPUTED from the link's (possibly just-autofilled) quantity — the comparable one.
            "unit_price_sek": _computed_unit_price(
                _effective_price(price_point), product_store.package_quantity
            ),
            # What the store printed — display only, never sorted on (D-05).
            "store_unit_price_sek": _as_float(price_point.store_unit_price_sek),
            "package_quantity": _as_float(product_store.package_quantity),
            # The page disagreed with the operator's typed amount; the stored value is
            # untouched (D-07) and the flag self-clears when either number is corrected.
            "quantity_mismatch": outcome.mismatch,
            "offer_price_sek": _as_float(price_point.offer_price_sek),
            "offer_type": price_point.offer_type,
            "in_stock": price_point.in_stock,
            "confidence": outcome.extraction.confidence,
        }

        # perform_price_check does not commit — the caller owns the transaction.
        await session.commit()

        return response
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
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        # 7 days, matching service.get_current_deals: most links are checked WEEKLY (Monday
        # offer-day schedule), so the old 24h window left this page empty from Tuesday on.
        # This route had kept 24h when the service was fixed — Gotcha 4's duplication again.
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)

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

        # Deduplicate by LINK (keep latest). The old key was the (product, store) pair, which
        # was single-valued only because of the constraint this phase dropped: a 24-pack and an
        # 8-pack of one product at one store are now legitimate, and the pair key collapsed
        # them into ONE arbitrary deal row — silently, with no error, one pack size simply
        # vanishing from the list.
        seen: set[uuid.UUID] = set()
        picked: list[tuple[PricePoint, Product, Store, ProductStore]] = []
        for price_point, product, store, product_store in rows:
            if product_store.id in seen:
                continue
            seen.add(product_store.id)
            picked.append((price_point, product, store, product_store))

        # Cheapest CURRENT jfr-pris per product across ALL its links (latest point per
        # link, no staleness cutoff — it is the platform's best knowledge). This is what
        # turns "20% rabatt" into a decision: the discount is the STORE's framing, the
        # cross-link unit price is ours.
        alternatives: dict[uuid.UUID, list[tuple[uuid.UUID, float, str, str | None]]] = {}
        if picked:
            product_ids = {product.id for _, product, _, _ in picked}
            latest = (
                select(
                    PricePoint.product_store_id.label("ps_id"),
                    func.max(PricePoint.checked_at).label("checked_at"),
                )
                .group_by(PricePoint.product_store_id)
                .subquery()
            )
            alt_stmt = (
                select(ProductStore, Store, PricePoint)
                .join(Store, ProductStore.store_id == Store.id)
                .join(latest, latest.c.ps_id == ProductStore.id)
                .join(
                    PricePoint,
                    (PricePoint.product_store_id == latest.c.ps_id)
                    & (PricePoint.checked_at == latest.c.checked_at),
                )
                .where(ProductStore.product_id.in_(product_ids))
            )
            for alt_ps, alt_store, alt_pp in (await session.execute(alt_stmt)).all():
                alt_unit_price = _computed_unit_price(
                    _effective_price(alt_pp), alt_ps.package_quantity
                )
                if alt_unit_price is None:
                    continue
                alternatives.setdefault(alt_ps.product_id, []).append(
                    (
                        alt_ps.id,
                        alt_unit_price,
                        link_store_name(alt_ps, alt_store),
                        alt_ps.package_size,
                    )
                )

        deals: list[DealResponse] = []
        for price_point, product, store, product_store in picked:
            # Calculate discount percentage
            discount_percent = 0.0
            if price_point.price_sek and price_point.offer_price_sek:
                discount_percent = (
                    (float(price_point.price_sek) - float(price_point.offer_price_sek))
                    / float(price_point.price_sek)
                    * 100
                )

            alts = [a for a in alternatives.get(product.id, []) if a[0] != product_store.id]
            best_alt = min(alts, key=lambda a: a[1]) if alts else None

            deals.append(
                DealResponse(
                    product_id=str(product.id),
                    product_name=product.name,
                    store_name=link_store_name(product_store, store),
                    store_slug=store.slug,
                    package_size=product_store.package_size,
                    unit=product.unit,
                    price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                    offer_price_sek=float(price_point.offer_price_sek),
                    # Exposed, not ranked on: the ordering below stays by RECENCY. Re-ranking
                    # deals by kr/unit is a behavior change to an unrelated feature; a consumer
                    # that wants to sort on this number now has it.
                    unit_price_sek=_computed_unit_price(
                        _effective_price(price_point), product_store.package_quantity
                    ),
                    # Swedish fallback — this string reaches the UI badge as-is.
                    offer_type=price_point.offer_type or "erbjudande",
                    offer_details=price_point.offer_details,
                    checked_at=price_point.checked_at.isoformat(),
                    discount_percent=discount_percent,
                    product_url=product_store.store_url,
                    best_alt_unit_price_sek=best_alt[1] if best_alt else None,
                    best_alt_store=best_alt[2] if best_alt else None,
                    best_alt_package_size=best_alt[3] if best_alt else None,
                )
            )

        return deals
    except Exception as e:
        LOGGER.exception("Failed to get current deals")
        raise HTTPException(status_code=500, detail="Failed to retrieve deals") from e


@router.get("/watches")
async def list_watches(
    tenant_id: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    """List price watches, optionally filtered by context.

    Args:
        tenant_id: Filter by context UUID. If not provided, defaults to user's context.
        session: Database session.

    Returns:
        List of price watch configurations.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
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
                        detail="Åtkomst nekad: du kan bara se bevakningar i din egen kontext",
                    )

                stmt = stmt.where(PriceWatch.tenant_id == tenant_uuid)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Ogiltigt format på tenant_id") from e

        stmt = stmt.where(PriceWatch.is_active.is_(True)).order_by(PriceWatch.created_at.desc())

        result = await session.execute(stmt)
        rows = result.all()

        # Current state per watched product — a watch without "how close is it?" is a
        # write-only register. Cheapest CURRENT effective price (offer wins) across the
        # product's links, latest point per link.
        current: dict[uuid.UUID, tuple[float, float | None, str]] = {}
        if rows:
            watched_ids = {product.id for _, product in rows}
            latest = (
                select(
                    PricePoint.product_store_id.label("ps_id"),
                    func.max(PricePoint.checked_at).label("checked_at"),
                )
                .group_by(PricePoint.product_store_id)
                .subquery()
            )
            cur_stmt = (
                select(ProductStore, Store, PricePoint)
                .join(Store, ProductStore.store_id == Store.id)
                .join(latest, latest.c.ps_id == ProductStore.id)
                .join(
                    PricePoint,
                    (PricePoint.product_store_id == latest.c.ps_id)
                    & (PricePoint.checked_at == latest.c.checked_at),
                )
                .where(ProductStore.product_id.in_(watched_ids))
            )
            for w_ps, w_store, w_pp in (await session.execute(cur_stmt)).all():
                effective = _effective_price(w_pp)
                if effective is None:
                    continue
                candidate = (
                    float(effective),
                    _computed_unit_price(effective, w_ps.package_quantity),
                    link_store_name(w_ps, w_store),
                )
                held = current.get(w_ps.product_id)
                if held is None or candidate[0] < held[0]:
                    current[w_ps.product_id] = candidate

        watches_list: list[dict[str, Any]] = []
        for watch, product in rows:
            now = current.get(product.id)
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
                    "unit": product.unit,
                    "target_price_sek": target_price,
                    "alert_on_any_offer": watch.alert_on_any_offer,
                    "price_drop_threshold_percent": watch.price_drop_threshold_percent,
                    "unit_price_target_sek": unit_price_target,
                    "unit_price_drop_threshold_percent": watch.unit_price_drop_threshold_percent,
                    "email_address": watch.email_address,
                    "last_alerted_at": last_alerted,
                    "created_at": watch.created_at.isoformat(),
                    "current_lowest_price_sek": now[0] if now else None,
                    "current_lowest_unit_price_sek": now[1] if now else None,
                    "current_lowest_store": now[2] if now else None,
                }
            )

        return watches_list
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list watches")
        raise HTTPException(status_code=500, detail="Failed to list watches") from e


@router.post("/watches", status_code=201)
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
        Requires IAP header auth (X-Auth-Request-Email).
        tenant_id must match the seeded tenant; email must be well-formed.
    """
    require_default_tenant(tenant_id)
    if not EMAIL_PATTERN.match(data.email_address):
        raise HTTPException(status_code=400, detail="Ogiltig e-postadress")
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
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på watch_id") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Prisbevakningen hittades inte")

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
                raise HTTPException(status_code=400, detail="Ogiltig e-postadress")
            watch.email_address = data.email_address

        await session.commit()
        return {"message": "Price watch updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to update watch %s", sanitize_log(watch_id))
        raise HTTPException(status_code=500, detail="Failed to update price watch") from e


@router.delete("/watches/{watch_id}")
async def delete_watch(
    watch_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a price watch.

    Args:
        watch_id: Watch UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt format på watch_id") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Prisbevakningen hittades inte")

        await session.delete(watch)
        await session.commit()

        return {"message": "Price watch deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to delete watch %s", sanitize_log(watch_id))
        raise HTTPException(status_code=500, detail="Failed to delete price watch") from e


@router.delete("/products/{product_id}")
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
        Requires IAP header auth (X-Auth-Request-Email).
    """
    try:
        await service.delete_product(product_id)
        return {"message": "Product deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Ogiltigt produkt-ID") from e
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
        session: Database session.

    Returns:
        JSON file download with Content-Disposition header.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
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
                    "check_weekdays": ps.check_weekdays,
                    "is_active": ps.is_active,
                    # The package data follows the LINK it describes.
                    "package_size": ps.package_size,
                    "package_quantity": _as_float(ps.package_quantity),
                    "scraped_package_quantity": _as_float(ps.scraped_package_quantity),
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

            # No package fields here: a product is the abstract good. Its packages are listed
            # on the store links above.
            product_dict: dict[str, Any] = {
                "id": str(product.id),
                "name": product.name,
                "brand": product.brand,
                "category": product.category,
                "unit": product.unit,
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
                                # The only unit price worth persisting in an export is the one
                                # actually OBSERVED on the page. The comparable kr/unit is
                                # derived from the link's quantity, so re-deriving it on import
                                # is correct behavior, not data loss (D-04).
                                "store_unit_price_sek": _as_float(pp.store_unit_price_sek),
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


@router.post(
    "/import",
)
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
        session: Database session.

    Returns:
        Summary with counts of created, updated, skipped items and any warnings.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
        Only imports data to the user's own context.
    """
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode måste vara 'merge' eller 'replace'")

    try:
        # Get user's context
        tenant_id = DEFAULT_TENANT_ID

        # Read and parse JSON
        content = await file.read()
        try:
            data = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Ogiltig JSON: {e}") from e

        # Validate version
        version = data.get("version")
        if version != "1.0":
            raise HTTPException(
                status_code=400, detail=f"Versionen av exporten stöds inte: {version}"
            )

        products_data = data.get("products", [])
        if not isinstance(products_data, list):
            raise HTTPException(status_code=400, detail="Ogiltigt format på products")

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
                # Update existing product. Package data is NOT a product attribute any more —
                # an import entry carries it on each store link instead.
                product.category = prod_data.get("category") or product.category
                product.unit = prod_data.get("unit") or product.unit
                products_updated += 1
            else:
                # Create new product
                product = Product(
                    tenant_id=tenant_id,
                    name=name,
                    brand=brand,
                    category=prod_data.get("category"),
                    unit=prod_data.get("unit"),
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

                # Check if link already exists. Dedupe on store_url — the link's natural key.
                # The old (product_id, store_id) pair is no longer single-valued: a product may
                # hold several links at one store (a 24-pack and an 8-pack), and resolving on
                # the pair raises MultipleResultsFound.
                ps_stmt = select(ProductStore).where(ProductStore.store_url == store_url)
                ps_result = await session.execute(ps_stmt)
                existing_ps = ps_result.scalar_one_or_none()

                if not existing_ps:
                    link_package_qty = None
                    if link_data.get("package_quantity"):
                        link_package_qty = Decimal(str(link_data["package_quantity"]))

                    # Create new link — the link owns the packaging.
                    ps = ProductStore(
                        product_id=product.id,
                        store_id=store.id,
                        store_url=store_url,
                        # Absent schedule fields = inherit the store's schedule (the old
                        # 168h import default predates store-level schedules).
                        check_frequency_hours=link_data.get("check_frequency_hours"),
                        check_weekdays=link_data.get("check_weekdays"),
                        is_active=link_data.get("is_active", True),
                        package_size=link_data.get("package_size"),
                        package_quantity=link_package_qty,
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
    session: AsyncSession = Depends(get_db),
):
    """Get price check scheduler status, statistics, and data freshness.

    `last_check_at` / `next_check_at` make the WEEKLY rhythm visible: prices refresh on
    the links' check days (Mondays for the offer chains), and the user should be able to
    see how fresh the numbers are and when the next round comes — not guess.

    Returns:
        Scheduler running state, last summary date, check stats, and freshness bounds.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    scheduler = request.app.state.scheduler
    status: dict[str, Any] = (
        {"running": False, "last_summary_date": None, "stats": None}
        if scheduler is None
        else scheduler.get_status()
    )
    try:
        bounds = (
            await session.execute(
                select(
                    func.max(ProductStore.last_checked_at),
                    func.min(ProductStore.next_check_at),
                ).where(ProductStore.is_active.is_(True))
            )
        ).first()
        last_check, next_check = bounds if bounds else (None, None)
        status["last_check_at"] = last_check.isoformat() if last_check else None
        status["next_check_at"] = next_check.isoformat() if next_check else None
    except Exception:
        # Freshness is decoration on a status endpoint — never let it break the page load.
        LOGGER.exception("Failed to read check freshness bounds")
        status.setdefault("last_check_at", None)
        status.setdefault("next_check_at", None)
    return status


@router.get("/logs")
async def get_logs(
    limit: int = 200,
    level: str = "INFO",
    admin_email: str = Depends(require_auth),
) -> dict[str, Any]:
    """Recent application log records (newest first) for the portal's Loggar page.

    Reads the in-memory ring buffer (infra.logbuffer) — the extraction path chosen,
    model fallbacks, WAF blocks, and other operational events the app already logs. The
    buffer is ephemeral (cleared on restart) and holds the app's own loggers only, not
    uvicorn access lines.

    Args:
        limit: Max records to return (1–1000).
        level: Minimum level — DEBUG / INFO / WARNING / ERROR / CRITICAL.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    limit = max(1, min(limit, 1000))
    records = get_log_buffer().get_records(limit=limit, min_level=level)
    return {"logs": records, "count": len(records)}


@router.get("/", response_class=HTMLResponse)
async def price_tracker_dashboard(admin_email: str = Depends(require_auth)) -> str:
    """Server-rendered admin dashboard for price tracking.
        HTML dashboard for managing products, deals, and price watches.

    Security:
        Requires IAP header auth (X-Auth-Request-Email).
    """
    template_path = Path(__file__).parent / "templates" / "admin.html"
    parts = template_path.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")

    content = parts[0] if len(parts) > 0 else ""
    extra_css = parts[1] if len(parts) > 1 else ""
    extra_js = parts[2] if len(parts) > 2 else ""

    base_css = _get_admin_nav_css()
    sidebar = _get_admin_sidebar_html()
    header = _get_admin_header_html(admin_email)
    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Price Tracker</title>
    <script src="/static/chart.umd.min.js"></script>
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
</html>"""


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
        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }
        .admin-layout { display: flex; min-height: 100vh; }
        .admin-sidebar {
            width: var(--nav-width);
            background: var(--bg-nav);
            color: var(--text-nav);
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            display: flex;
            flex-direction: column;
            z-index: 100;
        }
        .sidebar-header { padding: 16px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .sidebar-logo {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .sidebar-logo span { font-size: 18px; }
        .sidebar-nav { flex: 1; overflow-y: auto; padding: 12px 0; }
        .nav-section {
            padding: 8px 16px 4px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #64748b;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            color: var(--text-nav);
            text-decoration: none;
            font-size: 13px;
            transition: all 0.15s;
            border-left: 3px solid transparent;
        }
        .nav-item:hover { background: rgba(255,255,255,0.05); color: #fff; }
        .nav-item.active {
            background: rgba(37, 99, 235, 0.2);
            color: var(--text-nav-active);
            border-left-color: var(--primary);
        }
        .nav-icon { font-size: 16px; width: 20px; text-align: center; }
        .sidebar-footer {
            padding: 12px 16px;
            border-top: 1px solid rgba(255,255,255,0.1);
            font-size: 11px;
            color: #64748b;
        }
        .admin-main {
            flex: 1;
            margin-left: var(--nav-width);
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }
        .admin-header {
            height: var(--header-height);
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
            position: sticky;
            top: 0;
            z-index: 50;
        }
        .breadcrumbs { display: flex; align-items: center; gap: 8px; font-size: 13px; }
        .breadcrumbs a { color: var(--text-muted); text-decoration: none; }
        .breadcrumbs a:hover { color: var(--primary); }
        .breadcrumbs .separator { color: var(--border); }
        .breadcrumbs .current { color: var(--text); font-weight: 500; }
        .header-actions { display: flex; align-items: center; gap: 12px; }
        .user-menu {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 12px;
            background: var(--bg);
            border-radius: 6px;
            font-size: 13px;
        }
        .user-avatar {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: var(--primary);
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 12px;
        }
        .admin-content { flex: 1; padding: 24px; }
        .page-title { font-size: 20px; font-weight: 600; margin-bottom: 20px; }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .card-title { font-size: 14px; font-weight: 600; }
        .btn {
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text);
            transition: all 0.15s;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .btn:hover { background: var(--bg); border-color: var(--text-muted); }
        .btn-primary { background: var(--primary); border-color: var(--primary); color: #fff; }
        .btn-primary:hover { background: var(--primary-dark); }
        .btn-sm { padding: 5px 10px; font-size: 12px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        th {
            background: var(--bg);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            color: var(--text-muted);
        }
        tr:hover { background: var(--bg); }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 500;
        }
        .badge-success { background: #d1fae5; color: #065f46; }
        .badge-warning { background: #fef3c7; color: #92400e; }
        .badge-error { background: #fee2e2; color: #991b1b; }
        .badge-info { background: #dbeafe; color: #1e40af; }
        .badge-muted { background: #e5e7eb; color: #374151; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .status-dot.ok { background: var(--success); }
        .status-dot.warning { background: var(--warning); }
        .status-dot.error { background: var(--error); }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }
        .stat-value { font-size: 24px; font-weight: 600; color: var(--primary); }
        .stat-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .loading {
            color: var(--text-muted);
            font-style: italic;
            text-align: center;
            padding: 20px;
        }
        .empty-state { text-align: center; padding: 40px 20px; color: var(--text-muted); }
        .empty-state-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
        .shared-toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            z-index: 9999;
            display: none;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            max-width: 400px;
        }
        .shared-toast.success { background: var(--success); display: block; }
        .shared-toast.error { background: var(--error); display: block; }
        .shared-toast.warning { background: var(--warning); display: block; }
        .shared-toast.info { background: var(--primary); display: block; }
        a.stat-box { text-decoration: none; display: block; color: inherit; }
        a.stat-box:hover { border-color: var(--primary); }
        /* Mobile: the app doubles as the shopping list IN the store, so the phone is a
           first-class surface. The fixed sidebar becomes a horizontal top bar, content
           gets the full width, and wide tables scroll inside their card (see the
           template CSS) instead of stretching the page. */
        @media (max-width: 768px) {
            .admin-layout { flex-direction: column; }
            .admin-sidebar {
                position: static;
                width: 100%;
                flex-direction: row;
                align-items: center;
            }
            .sidebar-header { padding: 10px 12px; border-bottom: none; }
            .sidebar-nav {
                display: flex;
                padding: 0;
                overflow-y: visible;
                overflow-x: auto;
            }
            .nav-item {
                border-left: none;
                border-bottom: 3px solid transparent;
                padding: 12px 10px;
                white-space: nowrap;
            }
            .nav-item.active {
                border-left-color: transparent;
                border-bottom-color: var(--primary);
            }
            .sidebar-footer { display: none; }
            .admin-main { margin-left: 0; }
            .admin-header { padding: 0 12px; }
            .user-menu span { display: none; }  /* avatar suffices on a phone */
            .admin-content { padding: 12px; }
            .card { padding: 12px; }
            .card-header { flex-wrap: wrap; gap: 8px; }
            .header-actions { flex-wrap: wrap; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 12px; }
            th, td { padding: 8px; }
        }
    """


def _get_admin_sidebar_html() -> str:
    """Sidebar with one nav item per page.

    The items are hash links, not routes: the app is one served page and the frontend's
    renderPage() toggles which section is visible (and which item is .active) — so the
    `active` class is owned by JS, never hardcoded here.
    """
    footer = f"Price Tracker v{_APP_VERSION}" if _APP_VERSION else "Price Tracker"
    return f"""
    <aside class="admin-sidebar">
        <div class="sidebar-header">
            <a href="/" class="sidebar-logo">
                <span>&#128181;</span>
                Price Tracker
            </a>
        </div>
        <nav class="sidebar-nav">
            <a href="#/erbjudanden" class="nav-item" data-page="erbjudanden">
                <span class="nav-icon">&#127991;&#65039;</span>
                Erbjudanden
            </a>
            <a href="#/produkter" class="nav-item" data-page="produkter">
                <span class="nav-icon">&#128230;</span>
                Produkter
            </a>
            <a href="#/bevakningar" class="nav-item" data-page="bevakningar">
                <span class="nav-icon">&#128276;</span>
                Bevakningar
            </a>
            <a href="#/loggar" class="nav-item" data-page="loggar">
                <span class="nav-icon">&#128220;</span>
                Loggar
            </a>
        </nav>
        <div class="sidebar-footer">
            {footer}
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
            <a href="/">Price Tracker</a>
            <span class="separator">/</span>
            <span class="current" id="breadcrumb-current">Aktuella erbjudanden</span>
        </div>
        <div class="header-actions">
            <div class="user-menu">
                <div class="user-avatar">{user_initial}</div>
                <span id="user-email">{safe_email}</span>
            </div>
        </div>
    </header>
    """
