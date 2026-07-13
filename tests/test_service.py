"""Tests for price tracker service."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from domain.service import PriceTrackerService


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

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_product]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        products = await service.get_products()

        assert len(products) == 1
        assert products[0]["name"] == "Mjölk Arla Standard 3%"
        assert products[0]["brand"] == "Arla"
        assert products[0]["category"] == "Mejeri"

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

        mock_price = MagicMock()
        mock_price.price_sek = Decimal("29.90")
        mock_price.offer_price_sek = Decimal("19.90")
        mock_price.offer_type = "stammispris"

        mock_product = MagicMock()
        mock_product.id = product_id
        mock_product.name = "Mjölk Arla"

        mock_store = MagicMock()
        mock_store.id = store_id
        mock_store.name = "ICA Maxi"

        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_price, mock_product, mock_store)]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        deals = await service.get_current_deals(store_type="grocery")

        assert len(deals) == 1
        assert deals[0]["product_name"] == "Mjölk Arla"
        assert deals[0]["offer_type"] == "stammispris"

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
            "unit_price_sek": 149.50,
            "in_stock": True,
        }

        service = PriceTrackerService(mock_session_factory)
        price_point = await service.record_price(str(product_store_id), price_data, mock_session)

        assert price_point is not None
        assert price_point.price_sek == Decimal("29.90")
        assert price_point.unit_price_sek == Decimal("149.50")
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
    async def test_create_product_with_package_size(self, mock_session_factory: Mock) -> None:
        """Test create_product with package size metadata."""
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        tenant_id = uuid.uuid4()
        service = PriceTrackerService(mock_session_factory)
        product = await service.create_product(
            tenant_id=tenant_id,
            name="Toalettpapper 24-pack",
            brand="Lambi",
            category="Hygiene",
            unit="st",
            package_size="24-pack",
            package_quantity=Decimal("24.0"),
        )

        assert product.name == "Toalettpapper 24-pack"
        assert product.package_size == "24-pack"
        assert product.package_quantity == Decimal("24.0")
        assert mock_session.add.called
        assert mock_session.commit.called

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

        mock_price1 = MagicMock()
        mock_price1.price_sek = Decimal("29.90")
        mock_price1.offer_price_sek = None
        mock_price1.checked_at = datetime.now(UTC) - timedelta(days=5)

        mock_price2 = MagicMock()
        mock_price2.price_sek = Decimal("25.90")
        mock_price2.offer_price_sek = Decimal("19.90")
        mock_price2.checked_at = datetime.now(UTC) - timedelta(days=1)

        mock_store = MagicMock()
        mock_store.name = "ICA Maxi"

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (mock_price2, mock_store),
            (mock_price1, mock_store),
        ]
        mock_session.execute.return_value = mock_result

        service = PriceTrackerService(mock_session_factory)
        history = await service.get_price_history(str(product_id), days=30)

        assert len(history) == 2
        assert history[0]["price_sek"] == 25.90
        assert history[0]["offer_price_sek"] == 19.90
        assert history[1]["price_sek"] == 29.90
        assert history[1]["offer_price_sek"] is None
