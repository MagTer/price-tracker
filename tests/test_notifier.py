"""Tests for price notifier module."""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from domain.deals import DealRow
from domain.notifier import PriceNotifier
from domain.protocols.email import EmailMessage, EmailResult


def _deal(
    product_name: str = "Mjolk Arla",
    store_name: str = "ICA Maxi",
    offer_price: float = 19.90,
    unit_price: float | None = None,
    unit: str | None = None,
    package_size: str | None = None,
    store_url: str = "https://example.com/produkt",
    verdict: str = "unknown",
    savings: float | None = None,
    best_alt_store: str | None = None,
    best_alt_package_size: str | None = None,
    best_alt_unit_price: float | None = None,
    checked_at: datetime | None = None,
    discount_percent: float = 10.0,
    offer_type: str = "kampanj",
    timing: str = "good",
    seen_cheaper: float | None = 0.0,
    lowest_unit_price: float | None = None,
    lowest_store: str | None = None,
) -> DealRow:
    """A DealRow as the scheduler hands them to the notifier (naive-UTC checked_at)."""
    return DealRow(
        product_id=uuid.uuid4(),
        product_name=product_name,
        product_store_id=uuid.uuid4(),
        store_name=store_name,
        store_slug="ica",
        store_url=store_url,
        package_size=package_size,
        unit=unit,
        price_sek=None,
        offer_price_sek=offer_price,
        unit_price_sek=unit_price,
        offer_type=offer_type,
        offer_details=None,
        checked_at=checked_at if checked_at is not None else datetime(2026, 7, 27, 8, 0, 0),
        discount_percent=discount_percent,
        best_alt_unit_price_sek=best_alt_unit_price,
        best_alt_store=best_alt_store,
        best_alt_package_size=best_alt_package_size,
        verdict=verdict,
        savings_per_unit_sek=savings,
        timing=timing,
        seen_cheaper_pct=seen_cheaper,
        lowest_unit_price_sek=lowest_unit_price,
        lowest_seen_at=datetime(2026, 7, 18, 8, 0, 0),
        lowest_store=lowest_store,
    )


# A "now" a few hours after the default checked_at — rows are FRESH unless a test says so.
_NOW = datetime(2026, 7, 27, 11, 0, 0)


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
        assert "Ditt målpris:" not in html
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

        html = notifier._build_summary_html(deals=[], watched_products=[], now=_NOW)

        # Should have basic structure — and say honestly that there is nothing to buy.
        assert "<!DOCTYPE html>" in html
        assert "Veckans inköpslista" in html
        assert "Inga köpvärda erbjudanden" in html
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_groups_deals_per_butik(self) -> None:
        """One section per butik = one leg of the shopping round. The store name is a
        HEADING, not a column — the email answers 'vilken butik ska jag åka till?'."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            _deal(product_name="Smor Bregott", store_name="Willys", offer_price=29.90),
            _deal(product_name="Mjolk Arla", store_name="ICA Maxi", offer_price=19.90),
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "<h3" in html
        assert "ICA Maxi" in html
        assert "Willys" in html
        assert "Mjolk Arla" in html
        assert "Smor Bregott" in html
        # Swedish money: decimal comma, always to the öre.
        assert "19,90 kr" in html
        assert "29,90 kr" in html
        # Butiker alphabetically — deterministic, not recency-ordered.
        assert html.index("ICA Maxi") < html.index("Willys")
        # Should NOT have watched products section
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_ranks_sure_wins_before_uncomparable(self) -> None:
        """Within a butik: BEST by margin (biggest win first), then UNKNOWN — the same
        order as the portal's 'Värt att köpa'."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            _deal(product_name="Okand mangd", store_name="Willys", verdict="unknown"),
            _deal(
                product_name="Liten vinst",
                store_name="Willys",
                verdict="best",
                unit_price=5.00,
                savings=0.10,
                best_alt_store="ICA",
            ),
            _deal(
                product_name="Stor vinst",
                store_name="Willys",
                verdict="best",
                unit_price=4.00,
                savings=2.50,
                best_alt_store="ICA",
            ),
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert html.index("Stor vinst") < html.index("Liten vinst") < html.index("Okand mangd")

    def test_build_summary_html_names_the_margin_and_the_alternative(self) -> None:
        """The verdict is a sentence you act on — '1,24 kr/st billigare än ICA 8-pack' —
        and an equal price says 'lika billigt', never a bare badge."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            _deal(
                product_name="Lambi",
                store_name="Willys",
                verdict="best",
                unit="st",
                unit_price=5.00,
                savings=1.24,
                best_alt_store="ICA",
                best_alt_package_size="8-pack",
            ),
            _deal(
                product_name="Bregott",
                store_name="Willys",
                verdict="best",
                unit="kg",
                unit_price=89.00,
                savings=0.0,
                best_alt_store="Coop",
            ),
            _deal(product_name="Sukrin", store_name="Willys", verdict="unknown"),
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "1,24 kr/st billigare än ICA 8-pack" in html
        assert "lika billigt som Coop" in html
        # UNKNOWN says WHY no comparison exists (this row has no kr/unit -> no amount).
        assert "kan inte jämföras — länken saknar mängd" in html

    def test_unknown_with_a_unit_price_never_claims_to_be_the_only_link(self) -> None:
        """best_alt is also None when sibling links exist but none is comparable (no mängd
        on them) — 'enda länken för produkten' was a false statement that hid the fix."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [_deal(product_name="Sukrin", verdict="unknown", unit="st", unit_price=5.0)]
        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "kan inte jämföras — ingen annan jämförbar länk" in html
        assert "enda länken" not in html

    def test_a_worse_deal_fed_defensively_gets_the_honest_sentence(self) -> None:
        """The scheduler filters WORSE out, but _ranked_store_groups keeps them for any
        caller that does not — and that row IS comparable, so it must say 'dyrare än',
        not the UNKNOWN wording."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            _deal(
                product_name="Kaffe",
                verdict="worse",
                unit="kg",
                unit_price=7.07,
                savings=-1.24,
                best_alt_store="Willys",
                best_alt_package_size="450 g",
            )
        ]
        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "1,24 kr/kg dyrare än Willys 450 g" in html
        assert "kan inte jämföras" not in html

    def test_a_poor_moment_fed_defensively_says_how_much_cheaper_it_has_been(self) -> None:
        """Same defensive honesty as WORSE: the scheduler drops a poor-moment row, but a
        caller that does not must not have it read as an unqualified recommendation. The
        row is still the cheapest link — what is wrong with it is the moment."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            _deal(
                product_name="Bryggkaffe",
                verdict="best",
                unit="kg",
                unit_price=144.44,
                savings=6.45,
                best_alt_store="Willys",
                timing="poor",
                seen_cheaper=31.3,
                lowest_unit_price=110.00,
                lowest_store="Willys",
            )
        ]
        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "har varit 31 % billigare" in html
        assert "110,00 kr/kg" in html
        assert "hos Willys" in html

    def test_build_summary_html_dates_stale_deals_in_swedish(self) -> None:
        """A deal seen more than 48h ago is dated ('sett fredag 24/7'), never hidden —
        the campaign may be over, and saying when we looked is the honest version."""
        notifier = PriceNotifier(email_service=MockEmailService())

        deals = [
            # Friday 2026-07-24 06:30 UTC — well over 48h before Monday 11:00.
            _deal(
                product_name="Gammal kampanj",
                store_name="Apotea",
                checked_at=datetime(2026, 7, 24, 6, 30, 0),
            ),
            # Monday morning — fresh, no dating.
            _deal(
                product_name="Farsk kampanj",
                store_name="Willys",
                checked_at=datetime(2026, 7, 27, 7, 0, 0),
            ),
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        assert "sett fredag 24/7" in html
        assert "kan ha hunnit ta slut" in html
        # Exactly ONE dated row — the fresh one carries no 'sett'.
        assert html.count("sett ") == 1

    def test_build_summary_html_links_deals_to_the_store_page(self) -> None:
        """The email is read in the aisle: the product name links to the store's page,
        and the jfr-pris renders under the offer price when known."""
        notifier = PriceNotifier(email_service=MockEmailService())
        deals = [
            _deal(
                product_name="Mjolk Arla",
                store_name="ICA Maxi",
                store_url="https://handlaprivatkund.ica.se/stores/1/products/mjolk",
                unit="liter",
                unit_price=13.27,
                offer_price=19.90,
            ),
        ]
        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)
        assert 'href="https://handlaprivatkund.ica.se/stores/1/products/mjolk"' in html
        assert "13,27 kr/liter" in html

    def test_build_summary_html_never_links_unsafe_schemes(self) -> None:
        """A javascript: store_url must degrade to plain text, not a link."""
        notifier = PriceNotifier(email_service=MockEmailService())
        deals = [
            _deal(
                product_name="Mjolk Arla",
                store_name="ICA Maxi",
                store_url="javascript:alert(1)",
            ),
        ]
        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)
        assert "javascript:" not in html
        assert "Mjolk Arla" in html

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
        assert "19,90 kr" in html  # Swedish decimal comma, same as the deal rows
        assert "ICA Maxi" in html
        assert "Smor Bregott Original" in html
        assert "Coop" in html

        # Should NOT have deals section
        assert "Aktuella erbjudanden" not in html

    def test_build_summary_html_renders_per_row_price_label(self) -> None:
        """A kr/enhet row shows its unit label; the absolute fallback stays plain kr."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": "Lambi Toalettpapper",
                "lowest_price": Decimal("5.83"),
                "store_name": "Willys",
                "price_label": "kr/st",
            },
            {
                "name": "Sukrin",
                "lowest_price": Decimal("129.00"),
                "store_name": "Apotea",
                "price_label": "kr",
            },
        ]

        html = notifier._build_summary_html(deals=[], watched_products=watched)

        assert "5,83 kr/st" in html
        assert "129,00 kr" in html
        assert "Lägsta pris" in html

    def test_watched_product_without_a_price_renders_a_dash_not_none(self) -> None:
        """A watch created before the first successful check has no price yet — the row
        must say so with an em dash, not interpolate a raw None ("None kr")."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        watched: list[dict[str, str | Decimal | None]] = [
            {"name": "Ny produkt", "lowest_price": None, "store_name": "", "price_label": None},
        ]

        html = notifier._build_summary_html(deals=[], watched_products=watched)

        assert "None" not in html
        assert "&mdash;" in html

    def test_build_summary_html_has_no_arbitrary_cap(self) -> None:
        """The buy list IS the shopping decision — a 'top 10 by recency' cap silently
        dropped real wins, so every row the scheduler hands over must render."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals = [
            _deal(product_name=f"Product {i}", store_name="Store", offer_price=10.00)
            for i in range(20)
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[], now=_NOW)

        for i in range(20):
            assert f"Product {i}" in html

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

        deals = [_deal(product_name="Mjolk", store_name="ICA")]
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
        assert "Veckans inköpslista" in sent_msg.subject
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

        deals = [
            _deal(
                product_name='<script>alert("deal")</script>',
                store_name='<img src=x onerror="bad()">',
                verdict="best",
                unit_price=5.0,
                savings=1.0,
                best_alt_store='<b onload="evil()">Alt</b>',
                package_size="<script>pack</script>",
            )
        ]
        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": '<iframe src="evil.com">Product</iframe>',
                "lowest_price": Decimal("29.90"),
                "store_name": '<a href="javascript:alert()">Store</a>',
            }
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=watched, now=_NOW)

        # All HTML should be escaped
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<img src=" not in html
        assert "&lt;img" in html
        assert "<iframe" not in html
        assert "&lt;iframe" in html
        # javascript: in href attribute should be escaped, not executable
        assert '<a href="javascript:' not in html  # Executable link blocked
