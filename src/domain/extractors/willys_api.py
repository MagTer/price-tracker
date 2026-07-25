"""Willys API price extractor."""

import logging
import re
from decimal import Decimal

import httpx

from domain.quickadd import parse_package_from_name
from domain.result import PriceExtractionResult, ProductMetadata

logger = logging.getLogger(__name__)

# Regex to extract product code from Willys URL
# Matches patterns like: /produkt/Some-Name-100014716_ST or /produkt/name-12345_ST
_PRODUCT_CODE_RE = re.compile(r"-(\d+_ST)(?:\?|$|#)")


class WillysApiExtractor:
    """Extract prices from Willys public REST API."""

    API_BASE = "https://www.willys.se/axfood/rest/p"
    TIMEOUT = 15.0

    async def extract(
        self, store_url: str, product_name: str | None = None
    ) -> PriceExtractionResult | None:
        """Extract price from Willys API.

        Returns None on any error to allow LLM fallback.
        """
        data = await self._fetch_product(store_url)
        if data is None:
            return None
        return self._parse_response(data)

    async def extract_metadata(self, store_url: str) -> ProductMetadata | None:
        """Identity + package for quick-add — the same structured API, read for the fields a
        price check ignores (name, brand, package size).

        Willys is a client-rendered SPA: its product HTML carries no JSON-LD and no price, so
        quick-add's HTML/LLM ladder sees an empty shell. The public REST API is the only
        reliable source, exactly as it is for the price-check path. Returns None on any error
        so the preview can still fall back to the HTML ladder.
        """
        data = await self._fetch_product(store_url)
        if data is None:
            return None

        name = data.get("name")
        if not name:
            return None

        # displayVolume ("1,1kg", "500 g", "6-pack") is the printed pack label — read it with
        # the same coded parser quick-add uses on a title, so amount/unit/pack_size land the
        # way the preview form expects.
        guess = parse_package_from_name(str(data.get("displayVolume") or ""))

        price_value = data.get("priceValue")
        return ProductMetadata(
            name=str(name),
            brand=str(data["manufacturer"]) if data.get("manufacturer") else None,
            category=None,  # Willys breadcrumbs aren't the app's taxonomy — leave it to the user.
            price_sek=Decimal(str(price_value)) if price_value is not None else None,
            package_amount=guess.amount,
            package_unit=guess.entry_unit,
            pack_size=guess.pack_size,
            confidence=0.99,
            source="willys_api",
            in_stock=not data.get("outOfStock", False),
        )

    async def _fetch_product(self, store_url: str) -> dict[str, object] | None:
        """GET the product JSON from the Willys REST API, or None on any error.

        Shared by the price and metadata paths so the URL→code→GET dance lives in one place.
        """
        code = self._extract_product_code(store_url)
        if not code:
            logger.debug("Could not extract product code from URL: %s", store_url)
            return None

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.get(
                    f"{self.API_BASE}/{code}",
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    logger.debug("Willys API returned %d for %s", resp.status_code, code)
                    return None
                return resp.json()
        except Exception:
            logger.debug("Willys API request failed for %s", store_url, exc_info=True)
            return None

    def _extract_product_code(self, url: str) -> str | None:
        """Extract product code from Willys URL."""
        match = _PRODUCT_CODE_RE.search(url)
        return match.group(1) if match else None

    def _parse_response(self, data: dict[str, object]) -> PriceExtractionResult:
        """Parse Willys API response into PriceExtractionResult."""
        price_value = data.get("priceValue")
        price_sek = Decimal(str(price_value)) if price_value is not None else None

        # Parse compare price. Formats seen: "33,29 kr", "33.29 kr",
        # "33,29 kr/kg", "12,50 kr/st" — grab the leading amount and drop any
        # unit suffix so "/kg" etc. never reaches Decimal.
        store_unit_price_sek: Decimal | None = None
        compare_price_str = data.get("comparePrice", "")
        if compare_price_str:
            match = re.search(r"[\d.,]+", str(compare_price_str))
            if match:
                cleaned = match.group(0).replace(",", ".")
                try:
                    store_unit_price_sek = Decimal(cleaned)
                except Exception:
                    logger.debug("Could not parse compare price: %s", compare_price_str)
            else:
                logger.debug("Could not parse compare price: %s", compare_price_str)

        # Check for offers
        offer_price_sek: Decimal | None = None
        offer_type: str | None = None
        offer_details: str | None = None

        savings = data.get("savingsAmount")
        if savings and price_sek:
            savings_dec = Decimal(str(savings))
            if savings_dec > 0:
                offer_price_sek = price_sek  # Current price IS the offer price
                price_sek = price_sek + savings_dec  # Reconstruct regular price
                offer_type = "kampanj"
                offer_details = f"Spara {savings} kr"

        # Stock status
        in_stock = not data.get("outOfStock", False)

        return PriceExtractionResult(
            price_sek=price_sek,
            store_unit_price_sek=store_unit_price_sek,
            offer_price_sek=offer_price_sek,
            offer_type=offer_type,
            offer_details=offer_details,
            in_stock=in_stock,
            confidence=0.99,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={"source": "willys_api"},
        )
