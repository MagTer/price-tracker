"""Tests for price tracker service."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from domain.result import PriceExtractionResult
from domain.service import PriceTrackerService, perform_price_check


@pytest.fixture
def mock_session_factory() -> Mock:
    """Create a mock session factory for testing."""
    factory = MagicMock()
    return factory


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async session."""
    session = AsyncMock()
    return session


def _make_link(
    package_size: str | None = None,
    package_quantity: Decimal | None = None,
    scraped_package_quantity: Decimal | None = None,
    store_id: uuid.UUID | None = None,
    store_url: str = "https://www.willys.se/produkt/lambi-24p",
) -> MagicMock:
    """Mock ProductStore (a link). Every attribute the row-dict builders read is set."""
    link = MagicMock()
    link.id = uuid.uuid4()
    link.store_id = store_id or uuid.uuid4()
    link.store_url = store_url
    link.package_size = package_size
    link.package_quantity = package_quantity
    link.scraped_package_quantity = scraped_package_quantity
    return link


def _make_price_point(
    price_sek: Decimal = Decimal("139.90"),
    offer_price_sek: Decimal | None = None,
    store_unit_price_sek: Decimal | None = None,
    offer_type: str | None = None,
    in_stock: bool = True,
) -> MagicMock:
    """Mock PricePoint."""
    pp = MagicMock()
    pp.price_sek = price_sek
    pp.offer_price_sek = offer_price_sek
    pp.store_unit_price_sek = store_unit_price_sek
    pp.offer_type = offer_type
    pp.in_stock = in_stock
    pp.checked_at = datetime.now(UTC) - timedelta(hours=1)
    return pp


class TestPriceTrackerService:
    """Tests for PriceTrackerService."""

    @pytest.mark.asyncio
    async def test_get_stores_returns_list(self, mock_session_factory: Mock) -> None:
        """Test get_stores returns list of active stores."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Create mock store with actual attributes
        store_id = uuid.uuid4()
        mock_store = MagicMock()
        mock_store.id = store_id
        mock_store.name = "ICA Maxi"
        mock_store.slug = "ica-maxi"
        mock_store.store_type = "grocery"

        # Mock execute result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_store]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        stores = await service.get_stores()

        assert len(stores) == 1
        assert stores[0]["name"] == "ICA Maxi"
        assert stores[0]["slug"] == "ica-maxi"
        assert stores[0]["store_type"] == "grocery"
        assert "id" in stores[0]

    @pytest.mark.asyncio
    async def test_get_stores_filters_inactive(self, mock_session_factory: Mock) -> None:
        """Test get_stores query filters by is_active."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        await service.get_stores()

        # Verify execute was called (query construction is verified by other tests)
        assert mock_session.execute.called

    @pytest.mark.asyncio
    async def test_get_products_with_empty_result(self, mock_session_factory: Mock) -> None:
        """Test get_products returns empty list when no products exist."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        products = await service.get_products()

        assert products == []

    @pytest.mark.asyncio
    async def test_get_products_returns_all_products(self, mock_session_factory: Mock) -> None:
        """Test get_products returns all products."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_product = MagicMock()
        mock_product.id = uuid.uuid4()
        mock_product.name = "Mjölk Arla Standard 3%"
        mock_product.brand = "Arla"
        mock_product.category = "Mejeri"
        mock_product.unit = "liter"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_product]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        products = await service.get_products()

        assert len(products) == 1
        assert products[0]["name"] == "Mjölk Arla Standard 3%"
        assert products[0]["brand"] == "Arla"
        assert products[0]["category"] == "Mejeri"
        # MCP needs the canonical unit to label a kr/unit column ("kr/liter").
        assert products[0]["unit"] == "liter"

    @pytest.mark.asyncio
    async def test_get_products_with_search_filter(self, mock_session_factory: Mock) -> None:
        """Test get_products with search filter calls execute."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        await service.get_products(search="Mjölk")

        # Verify execute was called
        assert mock_session.execute.called

    @pytest.mark.asyncio
    async def test_get_current_deals_with_empty_result(self, mock_session_factory: Mock) -> None:
        """Test get_current_deals returns empty list when no deals exist."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        deals = await service.get_current_deals()

        assert deals == []

    @pytest.mark.asyncio
    async def test_get_current_deals_with_store_type_filter(
        self, mock_session_factory: Mock
    ) -> None:
        """Test get_current_deals with store_type filter."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        product_id = uuid.uuid4()
        store_id = uuid.uuid4()

        mock_price = _make_price_point(
            price_sek=Decimal("29.90"),
            offer_price_sek=Decimal("19.90"),
            offer_type="stammispris",
        )
        mock_link = _make_link(
            package_size="1 liter", package_quantity=Decimal("1"), store_id=store_id
        )

        mock_product = MagicMock()
        mock_product.id = product_id
        mock_product.name = "Mjölk Arla"

        mock_store = MagicMock()
        mock_store.id = store_id
        mock_store.name = "ICA Maxi"

        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_price, mock_link, mock_product, mock_store)]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        deals = await service.get_current_deals(store_type="grocery")

        assert len(deals) == 1
        assert deals[0]["product_name"] == "Mjölk Arla"
        assert deals[0]["offer_type"] == "stammispris"
        assert deals[0]["package_size"] == "1 liter"
        # The offer price is what you actually pay, so it is what kr/unit is computed from.
        assert deals[0]["unit_price_sek"] == 19.90

    @pytest.mark.asyncio
    async def test_deals_two_links_same_store(self, mock_session_factory: Mock) -> None:
        """Two DIFFERENT links at the SAME store yield TWO deal rows, not one.

        Regression guard for the dedupe key. It used to be the (product.id, store.id) tuple,
        which was single-valued only because of the constraint D-01 drops — so a 24-pack and an
        8-pack of the same product at the same store silently collapsed into one arbitrary deal
        row. The static gate cannot see this: it detects a select() constrained on both columns,
        and this was a Python-side tuple key, an entirely different shape.
        """
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        product_id = uuid.uuid4()
        store_id = uuid.uuid4()

        mock_product = MagicMock()
        mock_product.id = product_id
        mock_product.name = "Lambi Toalettpapper"

        mock_store = MagicMock()
        mock_store.id = store_id
        mock_store.name = "Willys"

        # Same product, same store, two pack sizes — two distinct links.
        link_24 = _make_link(
            package_size="24-pack",
            package_quantity=Decimal("24"),
            store_id=store_id,
            store_url="https://www.willys.se/produkt/lambi-24p",
        )
        link_8 = _make_link(
            package_size="8-pack",
            package_quantity=Decimal("8"),
            store_id=store_id,
            store_url="https://www.willys.se/produkt/lambi-8p",
        )
        price_24 = _make_price_point(
            price_sek=Decimal("159.90"), offer_price_sek=Decimal("139.90"), offer_type="kampanj"
        )
        price_8 = _make_price_point(
            price_sek=Decimal("64.90"), offer_price_sek=Decimal("59.90"), offer_type="kampanj"
        )

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (price_24, link_24, mock_product, mock_store),
            (price_8, link_8, mock_product, mock_store),
        ]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        deals = await service.get_current_deals()

        assert len(deals) == 2
        assert {d["package_size"] for d in deals} == {"24-pack", "8-pack"}
        by_size = {d["package_size"]: d for d in deals}
        assert by_size["24-pack"]["unit_price_sek"] == 5.83  # 139.90 / 24
        assert by_size["8-pack"]["unit_price_sek"] == 7.49  # 59.90 / 8

    @pytest.mark.asyncio
    async def test_record_price_success(self, mock_session_factory: Mock) -> None:
        """Test record_price creates PricePoint successfully."""
        mock_session = AsyncMock()
        product_store_id = uuid.uuid4()

        # Mock ProductStore exists
        mock_product_store = MagicMock()
        mock_product_store.id = product_store_id
        mock_product_store.last_checked_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_product_store
        mock_session.execute.return_value = mock_result

        price_data = {
            "price_sek": 29.90,
            "store_unit_price_sek": 149.50,
            "in_stock": True,
        }

        service = PriceTrackerService(mock_session_factory)
        price_point = await service.record_price(str(product_store_id), price_data, mock_session)

        assert price_point is not None
        assert price_point.price_sek == Decimal("29.90")
        # What the store PRINTED (D-05) — persisted as-is, never sorted on. The computed
        # kr/unit is not stored at all (D-04).
        assert price_point.store_unit_price_sek == Decimal("149.50")
        assert price_point.in_stock is True

    @pytest.mark.asyncio
    async def test_record_price_invalid_product_store_id(self, mock_session_factory: Mock) -> None:
        """Test record_price returns None for invalid ProductStore ID."""
        mock_session = AsyncMock()

        # Mock ProductStore does not exist
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        price_data = {"price_sek": 29.90, "in_stock": True}

        service = PriceTrackerService(mock_session_factory)
        result = await service.record_price(str(uuid.uuid4()), price_data, mock_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_create_product(self, mock_session_factory: Mock) -> None:
        """Test create_product creates new product."""
        import uuid

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        tenant_id = uuid.uuid4()
        service = PriceTrackerService(mock_session_factory)
        product = await service.create_product(
            tenant_id=tenant_id,
            name="Smör Bregott",
            brand="Arla",
            category="Mejeri",
            unit="kg",
        )

        assert product.tenant_id == tenant_id
        assert product.name == "Smör Bregott"
        assert product.brand == "Arla"
        assert product.category == "Mejeri"
        assert product.unit == "kg"
        assert mock_session.add.called
        assert mock_session.commit.called

    @pytest.mark.asyncio
    async def test_create_product_rejects_package_kwargs(self, mock_session_factory: Mock) -> None:
        """create_product no longer accepts package data — it belongs to the link (MODEL-01)."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        tenant_id = uuid.uuid4()
        service = PriceTrackerService(mock_session_factory)

        with pytest.raises(TypeError):
            await service.create_product(
                tenant_id=tenant_id,
                name="Lambi Toalettpapper",
                brand="Lambi",
                category="Hygiene",
                unit="st",
                package_quantity=Decimal("24.0"),  # type: ignore[call-arg]
            )

    @pytest.mark.asyncio
    async def test_link_product_store_with_package_size(self, mock_session_factory: Mock) -> None:
        """Package data is created on the LINK — a 24-pack is a listing, not a product."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        product_id = uuid.uuid4()
        store_id = uuid.uuid4()

        service = PriceTrackerService(mock_session_factory)
        product_store = await service.link_product_store(
            str(product_id),
            str(store_id),
            "https://www.willys.se/produkt/lambi-24p",
            package_size="24-pack",
            package_quantity=Decimal("24"),
        )

        assert product_store.package_size == "24-pack"
        assert product_store.package_quantity == Decimal("24")
        assert mock_session.add.called
        assert mock_session.commit.called

    @pytest.mark.asyncio
    async def test_link_product_store_package_quantity_may_be_null(
        self, mock_session_factory: Mock
    ) -> None:
        """A NULL quantity is legitimate (D-02) — the first scrape autofills it."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        service = PriceTrackerService(mock_session_factory)
        product_store = await service.link_product_store(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "https://www.willys.se/produkt/lambi-8p",
        )

        assert product_store.package_quantity is None
        assert product_store.package_size is None

    @pytest.mark.asyncio
    async def test_link_product_store(self, mock_session_factory: Mock) -> None:
        """Test link_product_store creates ProductStore link."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        product_id = uuid.uuid4()
        store_id = uuid.uuid4()

        service = PriceTrackerService(mock_session_factory)
        product_store = await service.link_product_store(
            str(product_id), str(store_id), "https://www.ica.se/handla/produkt/test-123"
        )

        assert product_store.product_id == product_id
        assert product_store.store_id == store_id
        assert product_store.store_url == "https://www.ica.se/handla/produkt/test-123"
        assert mock_session.add.called
        assert mock_session.commit.called

    @pytest.mark.asyncio
    async def test_create_watch(self, mock_session_factory: Mock) -> None:
        """Test create_watch creates PriceWatch."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        tenant_id = uuid.uuid4()
        product_id = uuid.uuid4()

        service = PriceTrackerService(mock_session_factory)
        watch = await service.create_watch(
            tenant_id=str(tenant_id),
            product_id=str(product_id),
            email="test@example.com",
            target_price=Decimal("25.00"),
            alert_on_any_offer=False,
        )

        assert watch.tenant_id == tenant_id
        assert watch.product_id == product_id
        assert watch.email_address == "test@example.com"
        assert watch.target_price_sek == Decimal("25.00")
        assert watch.alert_on_any_offer is False
        assert mock_session.add.called
        assert mock_session.commit.called

    @pytest.mark.asyncio
    async def test_get_price_history(self, mock_session_factory: Mock) -> None:
        """Test get_price_history retrieves price points."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        product_id = uuid.uuid4()

        mock_price1 = _make_price_point(price_sek=Decimal("29.90"))
        mock_price1.checked_at = datetime.now(UTC) - timedelta(days=5)

        mock_price2 = _make_price_point(
            price_sek=Decimal("25.90"), offer_price_sek=Decimal("19.90")
        )
        mock_price2.checked_at = datetime.now(UTC) - timedelta(days=1)

        mock_link = _make_link(package_size="1 liter", package_quantity=Decimal("1"))

        mock_store = MagicMock()
        mock_store.name = "ICA Maxi"
        mock_store.slug = "ica-maxi"

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (mock_price2, mock_link, mock_store),
            (mock_price1, mock_link, mock_store),
        ]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        history = await service.get_price_history(str(product_id), days=30)

        assert len(history) == 2
        assert history[0]["price_sek"] == 25.90
        assert history[0]["offer_price_sek"] == 19.90
        assert history[1]["price_sek"] == 29.90
        assert history[1]["offer_price_sek"] is None

    @pytest.mark.asyncio
    async def test_get_price_history_carries_the_keys_mcp_reads(
        self, mock_session_factory: Mock
    ) -> None:
        """The four keys MCP reads off these rows — two of which have NEVER existed.

        compare_stores reads `unit_price_sek` and `in_stock` from get_price_history rows and has
        found neither since extraction, so its Jämförspris column printed N/A and its Lager column
        "Nej" for every row. This is the precondition for MODEL-07.
        """
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # 24 rolls at 139.90 → 5.83 kr/rulle computed. The store PRINTED 5.83/rulle.
        mock_price = _make_price_point(
            price_sek=Decimal("139.90"),
            store_unit_price_sek=Decimal("5.83"),
            in_stock=True,
        )
        mock_link = _make_link(package_size="24-pack", package_quantity=Decimal("24"))
        mock_store = MagicMock()
        mock_store.name = "Willys"
        mock_store.slug = "willys"

        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_price, mock_link, mock_store)]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        history = await service.get_price_history(str(uuid.uuid4()))

        row = history[0]
        for key in ("product_store_id", "unit_price_sek", "store_unit_price_sek", "in_stock"):
            assert key in row, f"MCP reads {key!r} off this row"
        assert row["product_store_id"] == str(mock_link.id)
        assert row["unit_price_sek"] == 5.83  # COMPUTED: 139.90 / 24
        assert row["store_unit_price_sek"] == 5.83  # what the store printed — a separate number
        assert row["in_stock"] is True
        assert row["package_size"] == "24-pack"
        assert row["package_quantity"] == 24.0

    @pytest.mark.asyncio
    async def test_get_price_history_null_quantity_yields_no_unit_price(
        self, mock_session_factory: Mock
    ) -> None:
        """A link with no amount yet (D-02) reports None, not a bogus number and not a crash."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        mock_price = _make_price_point(price_sek=Decimal("59.90"))
        mock_link = _make_link(package_quantity=None)
        mock_store = MagicMock()
        mock_store.name = "ICA"
        mock_store.slug = "ica"

        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_price, mock_link, mock_store)]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        history = await service.get_price_history(str(uuid.uuid4()))

        assert history[0]["unit_price_sek"] is None
        assert history[0]["package_quantity"] is None

    @pytest.mark.asyncio
    async def test_get_links_for_product_flags_links_needing_an_amount(
        self, mock_session_factory: Mock
    ) -> None:
        """One row per link, each carrying a computed kr/unit; needs_amount tracks a NULL qty."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        store = MagicMock()
        store.name = "Willys"
        store.slug = "willys"

        link_24 = _make_link(
            package_size="24-pack",
            package_quantity=Decimal("24"),
            scraped_package_quantity=Decimal("24"),
        )
        link_unknown = _make_link(package_size=None, package_quantity=None)

        price_24 = _make_price_point(price_sek=Decimal("139.90"))
        price_unknown = _make_price_point(price_sek=Decimal("59.90"))

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (link_24, store, price_24),
            (link_unknown, store, price_unknown),
        ]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        links = await service.get_links_for_product(str(uuid.uuid4()))

        assert len(links) == 2

        priced, needs_amount = links
        assert priced["unit_price_sek"] == 5.83  # 139.90 / 24
        assert priced["needs_amount"] is False
        assert priced["quantity_mismatch"] is False

        # NULL quantity: no unit price, and the row says so out loud (D-02).
        assert needs_amount["needs_amount"] is True
        assert needs_amount["unit_price_sek"] is None
        assert needs_amount["price_sek"] == 59.90

    @pytest.mark.asyncio
    async def test_get_links_for_product_surfaces_quantity_mismatch(
        self, mock_session_factory: Mock
    ) -> None:
        """D-09's derived flag: the page disagrees with the operator, and the row says so."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        store = MagicMock()
        store.name = "Willys"
        store.slug = "willys"

        link = _make_link(
            package_size="24-pack",
            package_quantity=Decimal("24"),
            scraped_package_quantity=Decimal("12"),
        )

        mock_result = MagicMock()
        mock_result.all.return_value = [(link, store, _make_price_point())]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        links = await service.get_links_for_product(str(uuid.uuid4()))

        assert links[0]["quantity_mismatch"] is True
        assert links[0]["scraped_package_quantity"] == 12.0
        # The operator's intent is untouched — evidence never rewrites it (D-07).
        assert links[0]["package_quantity"] == 24.0


# ---------------------------------------------------------------------------
# perform_price_check — THE single fetch→extract→enrich→apply_scrape→record flow
# ---------------------------------------------------------------------------


def _extraction(
    price: str | None = "139.90",
    *,
    offer: str | None = None,
    package_amount: str | None = None,
    package_unit: str | None = None,
    source: str | None = None,
    confidence: float = 0.9,
) -> PriceExtractionResult:
    raw: dict = {}
    if source is not None:
        raw["source"] = source
    return PriceExtractionResult(
        price_sek=Decimal(price) if price is not None else None,
        store_unit_price_sek=None,
        offer_price_sek=Decimal(offer) if offer is not None else None,
        offer_type="kampanj" if offer is not None else None,
        offer_details=None,
        in_stock=True,
        confidence=confidence,
        pack_size=None,
        package_amount=Decimal(package_amount) if package_amount is not None else None,
        package_unit=package_unit,
        raw_response=raw,
    )


def _check_fixtures(
    *,
    package_quantity: Decimal | None = None,
    scraped_package_quantity: Decimal | None = None,
    prior_point: MagicMock | None = None,
):
    """(product_store, product, store, session, fetcher, parser) wired for one check."""
    product_store = MagicMock()
    product_store.id = uuid.uuid4()
    product_store.store_url = "https://www.apotea.se/produkt"
    product_store.package_quantity = package_quantity
    product_store.scraped_package_quantity = scraped_package_quantity
    product_store.last_checked_at = None

    product = MagicMock()
    product.name = "Lambi Toalettpapper"
    product.unit = "st"

    store = MagicMock()
    store.name = "Apotea"
    store.slug = "apotea"

    session = AsyncMock()
    prior_result = MagicMock()
    prior_result.scalars.return_value.first.return_value = prior_point
    session.execute = AsyncMock(return_value=prior_result)

    fetcher = AsyncMock()
    fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page text", "html": "<html>"})

    parser = MagicMock()
    parser.extract_price = AsyncMock()
    parser.enrich_with_llm = AsyncMock(side_effect=lambda base, **kwargs: base)

    return product_store, product, store, session, fetcher, parser


def _prior(price: str = "139.90", offer: str | None = None) -> MagicMock:
    pp = MagicMock()
    pp.price_sek = Decimal(price)
    pp.offer_price_sek = Decimal(offer) if offer is not None else None
    return pp


class TestPerformPriceCheck:
    @pytest.mark.asyncio
    async def test_success_records_point_and_applies_scrape(self) -> None:
        """Happy path: point added, link autofilled (D-07), no commit (caller's job)."""
        ps, product, store, session, fetcher, parser = _check_fixtures()
        parser.extract_price.return_value = _extraction(
            "139.90", package_amount="24", package_unit="st", source="llm"
        )

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        assert outcome.success is True
        assert outcome.failure_reason is None
        session.add.assert_called_once()
        assert outcome.price_point is session.add.call_args.args[0]
        assert outcome.price_point.price_sek == Decimal("139.90")
        # D-07 autofill ran (after enrichment, inside the flow)
        assert ps.package_quantity == Decimal("24")
        assert ps.last_checked_at is not None
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_outcome(self) -> None:
        ps, product, store, session, fetcher, parser = _check_fixtures()
        fetcher.fetch = AsyncMock(return_value={"ok": False, "error": "boom"})

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        assert outcome.success is False
        assert outcome.failure_reason == "fetch_failed"
        assert outcome.fetch_error == "boom"
        parser.extract_price.assert_not_called()
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_price_outcome(self) -> None:
        ps, product, store, session, fetcher, parser = _check_fixtures()
        parser.extract_price.return_value = _extraction(None, confidence=0.3)

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        assert outcome.success is False
        assert outcome.failure_reason == "no_price"
        assert outcome.extraction.confidence == 0.3
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_fires_on_first_success_without_quantity(self) -> None:
        """Trigger (a): jsonld result, no prior point, quantity-less link -> enrich."""
        ps, product, store, session, fetcher, parser = _check_fixtures(prior_point=None)
        parser.extract_price.return_value = _extraction("108.95", source="jsonld")

        outcome = await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        parser.enrich_with_llm.assert_awaited_once()
        kwargs = parser.enrich_with_llm.call_args.kwargs
        assert kwargs["text_content"] == "page text"  # the already-fetched page
        assert kwargs["store_slug"] == "apotea"
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_enrichment_fires_on_quantity_less_link_with_history(self) -> None:
        """Trigger (a) second clause: history exists but the link never got a quantity."""
        ps, product, store, session, fetcher, parser = _check_fixtures(
            package_quantity=None,
            scraped_package_quantity=None,
            prior_point=_prior("108.95"),
        )
        parser.extract_price.return_value = _extraction("108.95", source="jsonld")

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        parser.enrich_with_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enrichment_fires_on_effective_price_drop(self) -> None:
        """Trigger (b): current effective price below the prior point's -> enrich."""
        ps, product, store, session, fetcher, parser = _check_fixtures(
            package_quantity=Decimal("16"),
            scraped_package_quantity=Decimal("16"),
            prior_point=_prior("108.95"),
        )
        parser.extract_price.return_value = _extraction("89.95", source="jsonld")

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        parser.enrich_with_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_enrichment_on_unchanged_or_raised_price(self) -> None:
        """Neither trigger: settled link (quantity known, history) at same/higher price."""
        for current in ("108.95", "129.95"):
            ps, product, store, session, fetcher, parser = _check_fixtures(
                package_quantity=Decimal("16"),
                scraped_package_quantity=Decimal("16"),
                prior_point=_prior("108.95"),
            )
            parser.extract_price.return_value = _extraction(current, source="jsonld")

            outcome = await perform_price_check(
                product_store=ps,
                product=product,
                store=store,
                session=session,
                fetcher=fetcher,
                parser=parser,
            )

            parser.enrich_with_llm.assert_not_awaited()
            assert outcome.success is True

    @pytest.mark.asyncio
    async def test_drop_compares_against_prior_offer_price(self) -> None:
        """The prior EFFECTIVE price is the offer when the prior point had one."""
        ps, product, store, session, fetcher, parser = _check_fixtures(
            package_quantity=Decimal("16"),
            scraped_package_quantity=Decimal("16"),
            prior_point=_prior("108.95", offer="79.95"),
        )
        # 89.95 is below the prior regular but ABOVE the prior offer -> no drop.
        parser.extract_price.return_value = _extraction("89.95", source="jsonld")

        await perform_price_check(
            product_store=ps,
            product=product,
            store=store,
            session=session,
            fetcher=fetcher,
            parser=parser,
        )

        parser.enrich_with_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_enrichment_for_non_jsonld_sources(self) -> None:
        """API/LLM extractions already carry all fields — never enriched."""
        for source in ("willys_api", "llm", None):
            ps, product, store, session, fetcher, parser = _check_fixtures(prior_point=None)
            parser.extract_price.return_value = _extraction("89.95", source=source)

            await perform_price_check(
                product_store=ps,
                product=product,
                store=store,
                session=session,
                fetcher=fetcher,
                parser=parser,
            )

            parser.enrich_with_llm.assert_not_awaited()
            # No prior-point query either — the triggers are jsonld-only.
            session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_service_has_no_check_price(self) -> None:
        """The third inline copy is gone — everything routes through perform_price_check."""
        assert not hasattr(PriceTrackerService, "check_price")
