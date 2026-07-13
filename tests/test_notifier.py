"""Tests for price notifier module."""

from decimal import Decimal

import pytest

from domain.protocols.email import EmailMessage, EmailResult
from domain.notifier import PriceNotifier


class MockEmailService:
    """Mock email service for testing."""

    def __init__(self, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.sent_messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> EmailResult:
        self.sent_messages.append(message)
        if self.should_succeed:
            return EmailResult(success=True, message_id="test_id")
        return EmailResult(success=False, error="Mock error")

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        results = []
        for msg in messages:
            results.append(await self.send(msg))
        return results

    def is_configured(self) -> bool:
        return True


class TestPriceNotifier:
    """Tests for PriceNotifier class."""

    def test_build_alert_html_contains_expected_content(self) -> None:
        """Test _build_alert_html generates valid HTML with all fields."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Mjolk Arla Standard 3%",
            store_name="ICA Maxi",
            current_price=Decimal("19.90"),
            target_price=Decimal("25.00"),
            offer_type="stammispris",
            offer_details="Kop 2 betala for 1",
            product_url="https://www.ica.se/handla/produkt/test-123",
        )

        # Check basic structure
        assert "<!DOCTYPE html>" in html
        assert '<html lang="sv">' in html
        assert "</html>" in html

        # Check content elements
        assert "Prisvarning!" in html
        assert "Mjolk Arla Standard 3%" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "25.00 kr" in html  # Target price
        assert "stammispris" in html
        assert "Kop 2 betala for 1" in html
        assert "https://www.ica.se/handla/produkt/test-123" in html
        assert "Se produkten" in html  # Link button

    def test_build_alert_html_without_optional_fields(self) -> None:
        """Test _build_alert_html without target price and offer."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Smor Bregott",
            store_name="Willys",
            current_price=Decimal("29.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url=None,
        )

        # Should still have basic content
        assert "Smor Bregott" in html
        assert "Willys" in html
        assert "29.90 kr" in html

        # Should NOT have optional fields
        assert "Ditt malpris:" not in html
        assert "Erbjudande:" not in html
        assert "Se produkten" not in html

    def test_build_alert_html_with_offer_but_no_details(self) -> None:
        """Test _build_alert_html with offer type but no details."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Yoghurt",
            store_name="Coop",
            current_price=Decimal("12.50"),
            target_price=None,
            offer_type="extrapris",
            offer_details=None,
            product_url=None,
        )

        assert "extrapris" in html
        # Offer type should be in badge
        assert 'style="background: #22c55e' in html

    def test_build_summary_html_handles_empty_lists(self) -> None:
        """Test _build_summary_html with empty deals and watched products."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_summary_html(deals=[], watched_products=[])

        # Should have basic structure
        assert "<!DOCTYPE html>" in html
        assert "Veckans prisoversikt" in html
        assert "Har ar en sammanfattning" in html

        # Should NOT have tables or sections for empty data
        assert "Aktuella erbjudanden" not in html
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_deals(self) -> None:
        """Test _build_summary_html with deals."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjolk Arla",
                "store_name": "ICA Maxi",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "stammispris",
            },
            {
                "product_name": "Smor Bregott",
                "store_name": "Willys",
                "offer_price_sek": Decimal("29.90"),
                "offer_type": "kampanj",
            },
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should have deals section
        assert "Aktuella erbjudanden" in html
        assert "Mjolk Arla" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "stammispris" in html
        assert "Smor Bregott" in html
        assert "Willys" in html
        assert "kampanj" in html

        # Should NOT have watched products section
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_watched_products(self) -> None:
        """Test _build_summary_html with watched products."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": "Mjolk Arla Standard 3%",
                "lowest_price": Decimal("19.90"),
                "store_name": "ICA Maxi",
            },
            {
                "name": "Smor Bregott Original",
                "lowest_price": Decimal("29.90"),
                "store_name": "Coop",
            },
        ]

        html = notifier._build_summary_html(deals=[], watched_products=watched)

        # Should have watched products section
        assert "Dina bevakade produkter" in html
        assert "Mjolk Arla Standard 3%" in html
        assert "19.90 kr" in html
        assert "ICA Maxi" in html
        assert "Smor Bregott Original" in html
        assert "Coop" in html

        # Should NOT have deals section
        assert "Aktuella erbjudanden" not in html

    def test_build_summary_html_limits_deals_to_top_10(self) -> None:
        """Test _build_summary_html limits deals to top 10."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": f"Product {i}",
                "store_name": "Store",
                "offer_price_sek": Decimal("10.00"),
                "offer_type": "kampanj",
            }
            for i in range(20)
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should contain first 10 products
        for i in range(10):
            assert f"Product {i}" in html

        # Should NOT contain products beyond 10
        for i in range(10, 20):
            assert f"Product {i}" not in html

    @pytest.mark.asyncio
    async def test_send_price_alert_success(self) -> None:
        """Test send_price_alert with successful send."""
        mock_service = MockEmailService(should_succeed=True)
        notifier = PriceNotifier(email_service=mock_service)

        result = await notifier.send_price_alert(
            to_email="user@example.com",
            product_name="Mjolk Arla",
            store_name="ICA Maxi",
            current_price=Decimal("19.90"),
            target_price=Decimal("25.00"),
            offer_type="stammispris",
            offer_details="Kop 2 betala for 1",
            product_url="https://www.ica.se/test",
        )

        assert result is True
        assert len(mock_service.sent_messages) == 1

        sent_msg = mock_service.sent_messages[0]
        assert sent_msg.to == ["user@example.com"]
        assert "Prisvarning: Mjolk Arla hos ICA Maxi" in sent_msg.subject
        assert "Mjolk Arla" in sent_msg.html_body

    @pytest.mark.asyncio
    async def test_send_price_alert_failure(self) -> None:
        """Test send_price_alert with failed send."""
        mock_service = MockEmailService(should_succeed=False)
        notifier = PriceNotifier(email_service=mock_service)

        result = await notifier.send_price_alert(
            to_email="user@example.com",
            product_name="Mjolk",
            store_name="ICA",
            current_price=Decimal("19.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_weekly_summary_success(self) -> None:
        """Test send_weekly_summary with successful send."""
        mock_service = MockEmailService(should_succeed=True)
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjolk",
                "store_name": "ICA",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "kampanj",
            }
        ]
        watched: list[dict[str, str | Decimal | None]] = [
            {"name": "Smor", "lowest_price": Decimal("29.90"), "store_name": "Coop"}
        ]

        result = await notifier.send_weekly_summary(
            to_email="user@example.com",
            deals=deals,
            watched_products=watched,
        )

        assert result is True
        assert len(mock_service.sent_messages) == 1

        sent_msg = mock_service.sent_messages[0]
        assert sent_msg.to == ["user@example.com"]
        assert "Veckans prisoversikt" in sent_msg.subject
        assert "Mjolk" in sent_msg.html_body
        assert "Smor" in sent_msg.html_body

    def test_build_alert_html_escapes_html_injection(self) -> None:
        """Test _build_alert_html escapes malicious HTML in product/store names."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name='<script>alert("XSS")</script>Evil Product',
            store_name='<img src=x onerror="alert(1)">Evil Store',
            current_price=Decimal("19.90"),
            target_price=None,
            offer_type='<b onload="malicious()">offer</b>',
            offer_details='<iframe src="evil.com"></iframe>',
            product_url="https://safe.example.com/product",
        )

        # Check that HTML is escaped (should see &lt; &gt; instead of < >)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<img" not in html
        assert "&lt;img" in html
        assert "<b onload=" not in html
        assert "&lt;b onload=" in html
        assert "<iframe" not in html
        assert "&lt;iframe" in html
        assert "Evil Product" in html  # Text content should still be there
        assert "Evil Store" in html

    def test_build_alert_html_blocks_javascript_url(self) -> None:
        """Test _build_alert_html blocks javascript: URLs."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Test Product",
            store_name="Test Store",
            current_price=Decimal("19.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url='javascript:alert("XSS")',
        )

        # Should NOT contain the link section if URL is invalid
        assert "Se produkten" not in html
        assert "javascript:" not in html

    def test_build_alert_html_allows_safe_urls(self) -> None:
        """Test _build_alert_html allows http/https URLs."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        # Test http
        html_http = notifier._build_alert_html(
            product_name="Test",
            store_name="Store",
            current_price=Decimal("10.00"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url="http://example.com/product",
        )
        assert "Se produkten" in html_http
        assert "http://example.com/product" in html_http

        # Test https
        html_https = notifier._build_alert_html(
            product_name="Test",
            store_name="Store",
            current_price=Decimal("10.00"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url="https://example.com/product",
        )
        assert "Se produkten" in html_https
        assert "https://example.com/product" in html_https

    def test_build_summary_html_escapes_html_injection(self) -> None:
        """Test _build_summary_html escapes malicious HTML in deals and products."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": '<script>alert("deal")</script>',
                "store_name": '<img src=x onerror="bad()">',
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "<b>evil</b>",
            }
        ]
        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": '<iframe src="evil.com">Product</iframe>',
                "lowest_price": Decimal("29.90"),
                "store_name": '<a href="javascript:alert()">Store</a>',
            }
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=watched)

        # All HTML should be escaped
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<img src=" not in html
        assert "&lt;img" in html
        assert "<iframe" not in html
        assert "&lt;iframe" in html
        # javascript: in href attribute should be escaped, not executable
        assert '<a href="javascript:' not in html  # Executable link blocked
