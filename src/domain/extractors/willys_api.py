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


# The API answers on the SAME host as the product pages, so a WAF wall here is the same wall.
# Statuses kept in step with infra.fetcher._WAF_BLOCK_STATUSES on purpose.
_WAF_BLOCK_STATUSES = frozenset({202, 403, 429})

# The politeness key for this endpoint. The ledger is keyed by store id everywhere else, but
# this extractor never sees one — and it must be throttled regardless, because a Willys price
# check makes TWO requests to www.willys.se (the page fetch in perform_price_check, then this
# API call from the extraction ladder) and only the first one used to spend a slot.
_LEDGER_KEY = "host:www.willys.se"
# Short spacing: this is a public JSON endpoint, not a rendered page, and both callers
# (background check, quick-add preview) have already waited on the store's own slot.
_MIN_INTERVAL_SECONDS = 3.0
_MAX_WAIT_SECONDS = 10.0


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

        # Spend a slot on the SHARED ledger. Imported here rather than at module scope so the
        # domain layer keeps its lazy dependency on infra (same shape as the scheduler's).
        from infra.providers import get_rate_limiter

        await get_rate_limiter().acquire(
            _LEDGER_KEY, _MIN_INTERVAL_SECONDS, max_wait=_MAX_WAIT_SECONDS
        )

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.get(
                    f"{self.API_BASE}/{code}",
                    headers={"Accept": "application/json"},
                )
                if resp.status_code in _WAF_BLOCK_STATUSES:
                    # A bot wall, not a missing product. It was logged at DEBUG alongside every
                    # ordinary 404, so a Willys block was invisible in prod and quietly became
                    # "the API missed" — a silent, pointless fall-through to the LLM.
                    logger.warning(
                        "Willys API bot wall — HTTP %d for %s; falling back (do not retry)",
                        resp.status_code,
                        code,
                    )
                    return None
                if resp.status_code != 200:
                    logger.warning("Willys API returned %d for %s", resp.status_code, code)
                    return None
                return resp.json()
        except Exception:
            logger.warning("Willys API request failed for %s", store_url, exc_info=True)
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

        # Check for offers. `priceValue` is the ORDINARIE (pre-campaign) price and
        # `savingsAmount` is the per-unit discount, so the price you actually pay during the
        # campaign is priceValue - savingsAmount. Verified live (Bearnaise 101283524_ST,
        # 2026-07-25): priceValue 21.29, savingsAmount 3.39, and 21.29 - 3.39 = 17.90 matches
        # the campaign price in potentialPromotions[].price exactly.
        #
        # The earlier code did the reverse — treated priceValue as the offer and ADDED the
        # saving to invent a "regular" price (24.68) that appears nowhere in the API — which
        # is how a campaign product recorded two wrong numbers and hid the real 17.90.
        offer_price_sek: Decimal | None = None
        offer_type: str | None = None
        offer_details: str | None = None

        savings = data.get("savingsAmount")
        if savings and price_sek:
            savings_dec = Decimal(str(savings))
            if savings_dec > 0:
                offer_price_sek = price_sek - savings_dec  # what you pay during the campaign
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
