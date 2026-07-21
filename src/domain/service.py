"""Price Tracker Service implementation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.models import PricePoint, PriceWatch, Product, ProductStore, Store, link_store_name
from domain.parser import PriceParser
from domain.pricing import (
    apply_scrape_to_link,
    quantity_mismatch,
    unit_price_expr,
    unit_price_py,
)
from domain.protocols import IFetcher
from domain.result import PriceExtractionResult

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


def _utc_now() -> datetime:
    """Return naive UTC datetime for database operations.

    Returns naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _as_float(value: Decimal | None) -> float | None:
    """Decimal -> float for a row dict, preserving None."""
    return float(value) if value is not None else None


def _computed_unit_price(price: Decimal | None, quantity: Decimal | None) -> float | None:
    """kr/unit for a row dict — the single definition (D-03), rounded at this presentation boundary.

    None when the link has no amount yet (D-02): the row says "needs amount", it does not lie
    with a zero.
    """
    value = unit_price_py(price, quantity)
    if value is None:
        return None
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _effective_price(price_point: PricePoint) -> Decimal | None:
    """The price actually paid: the offer when there is one, else the regular price."""
    if price_point.offer_price_sek is not None:
        return price_point.offer_price_sek
    return price_point.price_sek


@dataclass
class PriceCheckOutcome:
    """What one price check produced — the scheduler and the admin endpoint both read this."""

    success: bool
    failure_reason: str | None  # "fetch_failed" | "no_price" | None
    fetch_error: str | None
    extraction: PriceExtractionResult | None
    price_point: PricePoint | None
    mismatch: str | None


async def perform_price_check(
    *,
    product_store: ProductStore,
    product: Product,
    store: Store,
    session: AsyncSession,
    fetcher: IFetcher,
    parser: PriceParser,
) -> PriceCheckOutcome:
    """THE fetch → extract → enrich → apply-scrape → record flow, exactly once.

    The scheduler and the admin "Check now" endpoint both call this — the third inline
    copy (service.check_price) is deleted. Does NOT commit: the caller owns the
    transaction (the scheduler batches per-item sessions, the endpoint uses the request
    session).
    """
    fetch_result = await fetcher.fetch(product_store.store_url)
    if not fetch_result.get("ok") or not fetch_result.get("text"):
        return PriceCheckOutcome(
            success=False,
            failure_reason="fetch_failed",
            fetch_error=str(fetch_result.get("error", "Unknown fetch error")),
            extraction=None,
            price_point=None,
            mismatch=None,
        )

    extraction = await parser.extract_price(
        text_content=fetch_result["text"],
        store_slug=store.slug,
        product_name=product.name,
        store_url=product_store.store_url,
        html_content=fetch_result.get("html"),
    )

    # LLM enrichment for the JSON-LD path (locked decision). JSON-LD carries only
    # price + stock, so offer-based watches and package autofill never see data for
    # the four JSON-LD stores without this. Two precise triggers keep the LLM cost
    # near zero; the API and LLM paths already carry all fields, and a discarded
    # extraction has no price.
    raw = extraction.raw_response if isinstance(extraction.raw_response, dict) else {}
    if extraction.price_sek is not None and raw.get("source") == "jsonld":
        # The link's latest prior point — keyed on product_store_id, never on the
        # (product_id, store_id) pair (no longer unique; the AST gate enforces this).
        prior_stmt = (
            select(PricePoint)
            .where(PricePoint.product_store_id == product_store.id)
            .order_by(PricePoint.checked_at.desc())
            .limit(1)
        )
        prior = (await session.execute(prior_stmt)).scalars().first()

        # Trigger (a): first successful check — no history yet, or the link still
        # has no quantity from any source (harvest the package fields once).
        first_success = prior is None or (
            product_store.package_quantity is None
            and product_store.scraped_package_quantity is None
        )
        # Trigger (b): the effective price dropped — an offer may have started
        # (harvest the offer fields so watches and find_deals can see it).
        price_drop = False
        if prior is not None:
            prior_effective = (
                prior.offer_price_sek if prior.offer_price_sek is not None else prior.price_sek
            )
            current_effective = (
                extraction.offer_price_sek
                if extraction.offer_price_sek is not None
                else extraction.price_sek
            )
            price_drop = prior_effective is not None and current_effective < prior_effective

        if first_success or price_drop:
            # Reuses the already-fetched page and never raises (parser guard):
            # the JSON-LD price is recorded whether or not enrichment succeeds.
            extraction = await parser.enrich_with_llm(
                extraction,
                text_content=fetch_result["text"],
                store_slug=store.slug,
                product_name=product.name,
            )

    if extraction.price_sek is None:
        return PriceCheckOutcome(
            success=False,
            failure_reason="no_price",
            fetch_error=None,
            extraction=extraction,
            price_point=None,
            mismatch=None,
        )

    # Runs AFTER enrichment so harvested package fields feed the D-07 autofill.
    # This is the ONLY scrape write path now: autofill when empty, flag on
    # conflict, never overwrite the operator's typed value.
    mismatch = apply_scrape_to_link(product_store, extraction, product.unit)
    if mismatch:
        logger.warning(f"Package quantity mismatch for {product.name} at {store.name}: {mismatch}")

    # store_unit_price_sek is what the STORE printed (D-05) — the computed kr/unit
    # is derived on read from the link's quantity and never persisted (D-04).
    price_point = PricePoint(
        product_store_id=product_store.id,
        price_sek=extraction.price_sek,
        store_unit_price_sek=extraction.store_unit_price_sek,
        offer_price_sek=extraction.offer_price_sek,
        offer_type=extraction.offer_type,
        offer_details=extraction.offer_details,
        in_stock=extraction.in_stock,
        raw_data=extraction.raw_response,
        checked_at=_utc_now(),
    )
    session.add(price_point)
    product_store.last_checked_at = _utc_now()

    return PriceCheckOutcome(
        success=True,
        failure_reason=None,
        fetch_error=None,
        extraction=extraction,
        price_point=price_point,
        mismatch=mismatch,
    )


class PriceTrackerService:
    """Service for managing price tracking operations.

    Handles product tracking, price history, and watch alerts.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize the price tracker service.

        Args:
            session_factory: SQLAlchemy async session factory.
        """
        self.session_factory = session_factory

    async def record_price(
        self, product_store_id: str, price_data: dict[str, Any], session: AsyncSession
    ) -> PricePoint | None:
        """Record a new price point for a product-store combination.

        Args:
            product_store_id: UUID string of the ProductStore.
            price_data: Dictionary containing price information:
                - price_sek: Regular price (required)
                - store_unit_price_sek: The store's PRINTED comparison price, as scraped
                  (optional; D-05). NOT the computed kr/unit — that is derived on read from
                  the link's package_quantity and is never persisted (D-04).
                - offer_price_sek: Offer price (optional)
                - offer_type: Type of offer (optional)
                - offer_details: Offer description (optional)
                - in_stock: Stock status (default: True)
                - raw_data: Raw scraped data (optional)
            session: Database session.

        Returns:
            Created PricePoint instance, or None if ProductStore not found.
        """
        try:
            product_store_uuid = uuid.UUID(product_store_id)

            # Verify ProductStore exists
            stmt = select(ProductStore).where(ProductStore.id == product_store_uuid)
            result = await session.execute(stmt)
            product_store = result.scalar_one_or_none()

            if not product_store:
                logger.warning(f"ProductStore {product_store_id} not found")
                return None

            # Create price point
            price_point = PricePoint(
                product_store_id=product_store_uuid,
                price_sek=Decimal(str(price_data["price_sek"])),
                store_unit_price_sek=(
                    Decimal(str(price_data["store_unit_price_sek"]))
                    if price_data.get("store_unit_price_sek")
                    else None
                ),
                offer_price_sek=(
                    Decimal(str(price_data["offer_price_sek"]))
                    if price_data.get("offer_price_sek")
                    else None
                ),
                offer_type=price_data.get("offer_type"),
                offer_details=price_data.get("offer_details"),
                in_stock=price_data.get("in_stock", True),
                raw_data=price_data.get("raw_data"),
                checked_at=_utc_now(),
            )

            session.add(price_point)

            # Update ProductStore last_checked_at
            product_store.last_checked_at = _utc_now()

            await session.commit()
            await session.refresh(price_point)

            logger.info(
                f"Recorded price {price_point.price_sek} SEK for ProductStore {product_store_id}"
            )
            return price_point

        except SQLAlchemyError:
            await session.rollback()
            logger.exception(f"Failed to record price for ProductStore {product_store_id}")
            return None

    async def get_price_history(
        self, product_id: str, days: int = 30
    ) -> list[dict[str, str | float | bool | datetime | None]]:
        """Get price history for a product across all of its links.

        Each row is one price observation on one LINK, and carries the COMPUTED kr/unit
        (D-03) derived from that link's package_quantity — not anything a store printed.
        `store_unit_price_sek` is the store's own claim, shown beside it and never sorted on
        (D-05).

        NB: `unit_price_sek` and `in_stock` are NEW keys here. MCP's compare_stores has read
        both off these rows since extraction and found neither, so its "Jämförspris" column has
        printed N/A and its "Lager" column "Nej" for every row (RESEARCH.md Pitfall 4).

        Args:
            product_id: UUID string of the product.
            days: Number of days of history to retrieve.

        Returns:
            List of price point dictionaries sorted by checked_at descending.
        """
        async with self.session_factory() as session:
            try:
                product_uuid = uuid.UUID(product_id)
                cutoff_date = _utc_now() - timedelta(days=days)

                # ProductStore is already joined for the product filter — adding it to the
                # select tuple costs no extra query, and it carries the quantity every
                # computed unit price in this method needs.
                stmt = (
                    select(PricePoint, ProductStore, Store)
                    .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                    .join(Store, ProductStore.store_id == Store.id)
                    .where(ProductStore.product_id == product_uuid)
                    .where(PricePoint.checked_at >= cutoff_date)
                    .order_by(PricePoint.checked_at.desc())
                )

                result = await session.execute(stmt)
                rows = result.all()

                return [
                    {
                        "checked_at": price_point.checked_at,
                        "product_store_id": str(product_store.id),
                        # The LINK's display name — two ICA-butik links must not both say "ICA".
                        "store_name": link_store_name(product_store, store),
                        "store_slug": store.slug,
                        "package_size": product_store.package_size,
                        "package_quantity": _as_float(product_store.package_quantity),
                        "price_sek": float(price_point.price_sek),
                        "offer_price_sek": _as_float(price_point.offer_price_sek),
                        "store_unit_price_sek": _as_float(price_point.store_unit_price_sek),
                        "unit_price_sek": _computed_unit_price(
                            _effective_price(price_point), product_store.package_quantity
                        ),
                        "in_stock": price_point.in_stock,
                    }
                    for price_point, product_store, store in rows
                ]

            except (SQLAlchemyError, ValueError):
                logger.exception(f"Failed to get price history for product {product_id}")
                return []

    async def get_links_for_product(
        self, product_id: str
    ) -> list[dict[str, str | float | bool | datetime | None]]:
        """One row per LINK for a product, ranked cheapest-per-unit first.

        This is the view that answers the phase's core question ("which pack size at which
        store is cheapest per roll"), and the single data source for both the product page and
        MCP's compare_stores. Each row carries the latest price observed on that link.

        Ranking is by the COMPUTED kr/unit with NULLs explicitly last: a link that still needs
        an amount (D-02) sinks to the bottom instead of masquerading as free. (Postgres already
        sorts NULLs last on ASC; saying so keeps the intent legible and survives a flip to DESC.)

        Args:
            product_id: UUID string of the product.

        Returns:
            List of link dictionaries, cheapest computed unit price first.
        """
        async with self.session_factory() as session:
            try:
                product_uuid = uuid.UUID(product_id)

                latest = (
                    select(
                        PricePoint.product_store_id.label("ps_id"),
                        func.max(PricePoint.checked_at).label("checked_at"),
                    )
                    .group_by(PricePoint.product_store_id)
                    .subquery()
                )

                unit_price = unit_price_expr(
                    PricePoint.effective_price_sek, ProductStore.package_quantity
                )

                # Outer-joined: a link with no price point yet is still a link, and still needs
                # to show its "needs amount" flag.
                stmt = (
                    select(ProductStore, Store, PricePoint)
                    .join(Store, ProductStore.store_id == Store.id)
                    .outerjoin(latest, latest.c.ps_id == ProductStore.id)
                    .outerjoin(
                        PricePoint,
                        (PricePoint.product_store_id == latest.c.ps_id)
                        & (PricePoint.checked_at == latest.c.checked_at),
                    )
                    .where(ProductStore.product_id == product_uuid)
                    .order_by(unit_price.asc().nulls_last())
                )

                result = await session.execute(stmt)
                rows = result.all()

                links: list[dict[str, str | float | bool | datetime | None]] = []
                for product_store, store, price_point in rows:
                    effective = _effective_price(price_point) if price_point else None
                    links.append(
                        {
                            "product_store_id": str(product_store.id),
                            "store_id": str(product_store.store_id),
                            "store_name": link_store_name(product_store, store),
                            # The raw label separately: the edit dialog needs to know
                            # whether the display name is a label or the chain fallback.
                            "store_label": product_store.store_label,
                            "store_slug": store.slug,
                            "store_url": product_store.store_url,
                            # Cadence, so the edit dialog can prefill and the row can show
                            # WHICH day/interval the link checks on.
                            "check_frequency_hours": product_store.check_frequency_hours,
                            "check_weekday": product_store.check_weekday,
                            "package_size": product_store.package_size,
                            "package_quantity": _as_float(product_store.package_quantity),
                            "scraped_package_quantity": _as_float(
                                product_store.scraped_package_quantity
                            ),
                            "price_sek": (float(price_point.price_sek) if price_point else None),
                            "offer_price_sek": (
                                _as_float(price_point.offer_price_sek) if price_point else None
                            ),
                            "store_unit_price_sek": (
                                _as_float(price_point.store_unit_price_sek) if price_point else None
                            ),
                            "unit_price_sek": _computed_unit_price(
                                effective, product_store.package_quantity
                            ),
                            "in_stock": price_point.in_stock if price_point else None,
                            "checked_at": price_point.checked_at if price_point else None,
                            # D-02's visible flag: the link is saveable without an amount, but
                            # it is never silently blank.
                            "needs_amount": product_store.package_quantity is None,
                            # D-09's derived, self-clearing flag — never persisted.
                            "quantity_mismatch": quantity_mismatch(product_store),
                        }
                    )

                return links

            except (SQLAlchemyError, ValueError):
                logger.exception(f"Failed to get links for product {product_id}")
                return []

    async def get_current_deals(
        self, store_type: str | None = None
    ) -> list[dict[str, str | float | None]]:
        """Get links whose LATEST price point is an offer, at most 7 days old.

        Latest-per-link is the join, not a Python dedupe: a superseded offer (a newer
        point on the same link without one) can never appear, because that newer point
        IS the link's latest row and has no offer. The 7-day bound is a staleness
        cutoff — the old fixed 24h window showed nothing between checks, since most
        links are checked weekly.

        Ordering stays RECENCY-based, deliberately. Now that kr/unit is computed it could rank
        these rows, but re-ranking find_deals is a behavior change to an unrelated feature; the
        decision here is to EXPOSE the number and not re-rank on it. Each row carries the
        computed `unit_price_sek` so a consumer that wants to sort by it can.

        Args:
            store_type: Filter by store type (grocery, pharmacy, etc.). Optional.

        Returns:
            List of deal dictionaries — one per LINK.
        """
        async with self.session_factory() as session:
            try:
                cutoff = _utc_now() - timedelta(days=7)

                latest = (
                    select(
                        PricePoint.product_store_id.label("ps_id"),
                        func.max(PricePoint.checked_at).label("checked_at"),
                    )
                    .group_by(PricePoint.product_store_id)
                    .subquery()
                )

                stmt = (
                    select(PricePoint, ProductStore, Product, Store)
                    .join(
                        latest,
                        (PricePoint.product_store_id == latest.c.ps_id)
                        & (PricePoint.checked_at == latest.c.checked_at),
                    )
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

                deals: list[dict[str, str | float | None]] = []

                for price_point, product_store, product, store in rows:
                    deals.append(
                        {
                            "product_id": str(product.id),
                            "product_name": product.name,
                            "product_store_id": str(product_store.id),
                            "store_name": link_store_name(product_store, store),
                            "package_size": product_store.package_size,
                            "regular_price_sek": float(price_point.price_sek),
                            "offer_price_sek": float(price_point.offer_price_sek),
                            "unit_price_sek": _computed_unit_price(
                                price_point.offer_price_sek, product_store.package_quantity
                            ),
                            "offer_type": price_point.offer_type or "unknown",
                        }
                    )

                return deals

            except SQLAlchemyError:
                logger.exception("Failed to get current deals")
                return []

    async def get_products(
        self, search: str | None = None, store_id: str | None = None
    ) -> list[dict[str, str]]:
        """List products with optional filtering.

        Args:
            search: Search term for product name/brand. Optional.
            store_id: Filter by specific store. Optional.

        Returns:
            List of product dictionaries.
        """
        async with self.session_factory() as session:
            try:
                stmt = select(Product)

                # Apply search filter
                if search:
                    search_term = f"%{search}%"
                    stmt = stmt.where(
                        or_(
                            Product.name.ilike(search_term),
                            Product.brand.ilike(search_term),
                        )
                    )

                # Apply store filter
                if store_id:
                    store_uuid = uuid.UUID(store_id)
                    stmt = (
                        stmt.join(ProductStore, Product.id == ProductStore.product_id)
                        .where(ProductStore.store_id == store_uuid)
                        .distinct()
                    )

                stmt = stmt.order_by(Product.name)
                result = await session.execute(stmt)
                products = result.scalars().all()

                return [
                    {
                        "id": str(product.id),
                        "name": product.name,
                        "brand": product.brand or "",
                        "category": product.category or "",
                        # The canonical comparison unit — what a kr/unit column is measured in
                        # ("kr/st"). MCP needs it to label that column.
                        "unit": product.unit or "",
                    }
                    for product in products
                ]

            except SQLAlchemyError:
                logger.exception("Failed to get products")
                return []

    async def get_store_names_by_product(self) -> dict[str, list[str]]:
        """Store names per product, in ONE query (no N+1).

        A product may hold several links at one store post-04.1 (different pack
        sizes), so names are deduped per product. Built for MCP list_products,
        whose "linked stores" column had read a key get_products never emitted —
        printing "Ej länkad" for every product since extraction.

        Returns:
            Mapping of str(product_id) -> sorted, deduped store names.
        """
        async with self.session_factory() as session:
            try:
                # The link's label wins over the chain name (link_store_name's rule, done
                # in SQL): two labeled ICA-butik links list as two distinct names.
                stmt = select(
                    ProductStore.product_id,
                    func.coalesce(ProductStore.store_label, Store.name),
                ).join(Store, ProductStore.store_id == Store.id)
                result = await session.execute(stmt)

                names: dict[str, set[str]] = {}
                for product_id, store_name in result.all():
                    names.setdefault(str(product_id), set()).add(store_name)

                return {product_id: sorted(n) for product_id, n in names.items()}

            except SQLAlchemyError:
                logger.exception("Failed to get store names by product")
                return {}

    async def get_stores(self) -> list[dict[str, str]]:
        """Get all active stores.

        Returns:
            List of store dictionaries.
        """
        async with self.session_factory() as session:
            try:
                stmt = select(Store).where(Store.is_active.is_(True)).order_by(Store.name)

                result = await session.execute(stmt)
                stores = result.scalars().all()

                return [
                    {
                        "id": str(store.id),
                        "name": store.name,
                        "slug": store.slug,
                        "store_type": store.store_type,
                    }
                    for store in stores
                ]

            except SQLAlchemyError:
                logger.exception("Failed to get stores")
                return []

    async def create_product(
        self,
        tenant_id: uuid.UUID,
        name: str,
        brand: str | None,
        category: str | None,
        unit: str | None,
    ) -> Product:
        """Create a new product — the abstract good, NOT a specific package.

        A product is "Lambi toalettpapper", not "Lambi toalettpapper 24-pack". `unit` is its
        canonical comparison unit (st / liter / kg) — the property that makes its per-store
        package listings comparable with one another.

        Package data belongs to the LINK (`link_product_store`), which is what actually
        describes a package: a 24-pack at Willys and an 8-pack at ICA are two listings of ONE
        product. Creating a separate product per size variant — which this method used to
        teach — is the model this design abolishes: it left the sizes unrelated, with no
        grouping key, so "cheapest kr/rulle across pack sizes" was unanswerable.

        Args:
            tenant_id: Context UUID for multi-tenancy.
            name: Product name.
            brand: Product brand. Optional.
            category: Product category. Optional.
            unit: Canonical comparison unit (st / liter / kg). Optional.

        Returns:
            Created Product instance.
        """
        async with self.session_factory() as session:
            product = Product(
                tenant_id=tenant_id,
                name=name,
                brand=brand,
                category=category,
                unit=unit,
            )

            session.add(product)
            await session.commit()
            await session.refresh(product)

            logger.info(f"Created product: {product.name} (ID: {product.id})")
            return product

    async def link_product_store(
        self,
        product_id: str,
        store_id: str,
        store_url: str,
        check_frequency_hours: int = 72,
        check_weekday: int | None = None,
        package_size: str | None = None,
        package_quantity: Decimal | None = None,
        store_label: str | None = None,
    ) -> ProductStore:
        """Link a product to a store — the concrete package listing at one store page.

        Args:
            product_id: UUID string of the product.
            store_id: UUID string of the store.
            store_url: URL to the product page on the store website.
            check_frequency_hours: How often to check price (default 72 hours / 3 days).
            check_weekday: Day of week to check (0=Monday, 6=Sunday). If set,
                           overrides frequency with weekly checks on that day.
            package_size: Human display label ("24-pack", "500 ml"). Optional.
            package_quantity: The amount in the package, in the PRODUCT's canonical unit —
                           every kr/unit in the app is computed from this number. Optional:
                           a NULL quantity is a legitimate state (D-02), not an error. The
                           link is saveable before its pack size is known, the first
                           successful scrape autofills it (D-07), and until then the link is
                           rendered "needs amount" rather than silently blank.
            store_label: Per-link store display name ("ICA Maxi Sandviken") for chains
                           with per-butik pricing. None = display the chain name.

        Returns:
            Created ProductStore instance.
        """
        async with self.session_factory() as session:
            product_uuid = uuid.UUID(product_id)
            store_uuid = uuid.UUID(store_id)

            product_store = ProductStore(
                product_id=product_uuid,
                store_id=store_uuid,
                store_url=store_url,
                check_frequency_hours=check_frequency_hours,
                check_weekday=check_weekday,
                package_size=package_size,
                package_quantity=package_quantity,
                store_label=store_label,
            )

            session.add(product_store)
            await session.commit()
            await session.refresh(product_store)

            logger.info(
                f"Linked product {product_id} to store {store_id} "
                f"(ProductStore ID: {product_store.id})"
            )
            return product_store

    async def create_watch(
        self,
        tenant_id: str,
        product_id: str,
        email: str,
        target_price: Decimal | None,
        alert_on_any_offer: bool,
        price_drop_threshold_percent: int | None = None,
        unit_price_target_sek: Decimal | None = None,
        unit_price_drop_threshold_percent: int | None = None,
    ) -> PriceWatch:
        """Create a price watch for a product.

        Args:
            tenant_id: UUID string of the context (multi-tenant).
            product_id: UUID string of the product.
            email: Email address for alerts.
            target_price: Target price threshold. Optional.
            alert_on_any_offer: Alert on any offer regardless of price.
            price_drop_threshold_percent: Alert when price drops by this percentage. Optional.
            unit_price_target_sek: Alert when unit price drops below threshold. Optional.
            unit_price_drop_threshold_percent: Alert when unit price drops by %. Optional.

        Returns:
            Created PriceWatch instance.
        """
        async with self.session_factory() as session:
            tenant_uuid = uuid.UUID(tenant_id)
            product_uuid = uuid.UUID(product_id)

            watch = PriceWatch(
                tenant_id=tenant_uuid,
                product_id=product_uuid,
                email_address=email,
                target_price_sek=target_price,
                alert_on_any_offer=alert_on_any_offer,
                price_drop_threshold_percent=price_drop_threshold_percent,
                unit_price_target_sek=unit_price_target_sek,
                unit_price_drop_threshold_percent=unit_price_drop_threshold_percent,
            )

            session.add(watch)
            await session.commit()
            await session.refresh(watch)

            logger.info(f"Created price watch for product {product_id} (Watch ID: {watch.id})")
            return watch

    async def delete_product(self, product_id: str) -> None:
        """Delete a product and all associated data.

        This cascades to delete:
        - ProductStore links (which cascade to PricePoints)
        - PriceWatches

        Args:
            product_id: UUID string of the product to delete.

        Raises:
            ValueError: If product_id is invalid or product not found.
        """
        async with self.session_factory() as session:
            try:
                product_uuid = uuid.UUID(product_id)
            except ValueError as e:
                raise ValueError(f"Invalid product_id format: {product_id}") from e

            # Get the product
            stmt = select(Product).where(Product.id == product_uuid)
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()

            if not product:
                raise ValueError(f"Product not found: {product_id}")

            # Delete the product (cascading will handle related records)
            await session.delete(product)
            await session.commit()

            logger.info(f"Deleted product {product_id} ({product.name})")


__all__ = ["PriceCheckOutcome", "PriceTrackerService", "perform_price_check"]
