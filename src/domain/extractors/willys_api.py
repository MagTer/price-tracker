"""Willys API price extractor."""

import logging
import re
from decimal import Decimal

import httpx

from domain.result import PriceExtractionResult

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

                data = resp.json()

            return self._parse_response(data)

        except Exception:
            logger.debug("Willys API extraction failed for %s", store_url, exc_info=True)
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
        unit_price_sek: Decimal | None = None
        compare_price_str = data.get("comparePrice", "")
        if compare_price_str:
            match = re.search(r"[\d.,]+", str(compare_price_str))
            if match:
                cleaned = match.group(0).replace(",", ".")
                try:
                    unit_price_sek = Decimal(cleaned)
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
            unit_price_sek=unit_price_sek,
            offer_price_sek=offer_price_sek,
            offer_type=offer_type,
            offer_details=offer_details,
            in_stock=in_stock,
            confidence=0.99,
            pack_size=None,
            raw_response={"source": "willys_api"},
        )
