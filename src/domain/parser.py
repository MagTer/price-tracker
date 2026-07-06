"""LLM-based price extraction with cost optimization."""

import json
import logging
import os
from decimal import Decimal

import httpx

from domain.extractors.base import PriceExtractor
from domain.extractors.willys_api import WillysApiExtractor
from domain.result import PriceExtractionResult
from infra.llm import OPENROUTER_BASE_URL, OPENROUTER_HEADERS

__all__ = ["PriceExtractionResult", "PriceParser"]

logger = logging.getLogger(__name__)


class PriceParser:
    """LLM-based price extractor with cascading model strategy."""

    # Cascading model strategy (fast first, then quality fallback)
    MODEL_CASCADE = os.getenv(
        "PRICE_PARSER_MODEL_CASCADE",
        "price_tracker,price_tracker_fallback",
    ).split(",")

    # Model-specific confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        "price_tracker": 0.70,
        "price_tracker_fallback": 0.0,  # Accept any confidence (last resort)
    }

    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self) -> None:
        self._store_hints: dict[str, str] = {}
        self._load_store_hints()
        self._api_extractors: dict[str, PriceExtractor] = {
            "willys": WillysApiExtractor(),
        }

    def _load_store_hints(self) -> None:
        """Load store-specific parsing hints."""
        from domain.stores import get_store_hints

        self._store_hints = get_store_hints()

    async def extract_price(
        self,
        text_content: str,
        store_slug: str,
        product_name: str | None = None,
        store_url: str | None = None,
    ) -> PriceExtractionResult:
        """Extract price data using API-first strategy with LLM cascade fallback."""

        # API-first: try structured extractor if available
        if store_url and store_slug in self._api_extractors:
            try:
                api_result = await self._api_extractors[store_slug].extract(store_url, product_name)
                if api_result is not None:
                    logger.info(
                        "Price extracted via API",
                        extra={
                            "method": "api",
                            "store": store_slug,
                            "product": product_name,
                            "confidence": api_result.confidence,
                        },
                    )
                    return api_result
                logger.debug(
                    "API extractor returned None, falling back to LLM",
                    extra={"store": store_slug},
                )
            except Exception as e:
                logger.warning(
                    "API extractor failed, falling back to LLM: %s",
                    e,
                    extra={"store": store_slug},
                )

        store_hint = self._store_hints.get(store_slug, "")

        prompt = self._build_prompt(text_content, store_slug, store_hint, product_name)

        # Try models in cascade order (cheapest to most expensive)
        last_result = None
        last_error = None

        for model_name in self.MODEL_CASCADE:
            threshold = self.CONFIDENCE_THRESHOLDS.get(model_name, 0.7)

            try:
                logger.debug(
                    f"Trying {model_name} (threshold: {threshold})",
                    extra={"product": product_name, "store": store_slug},
                )

                result = await self._extract_with_model(prompt, model_name)
                last_result = result

                if result.confidence >= threshold:
                    logger.info(
                        f"Price extracted with {model_name}",
                        extra={
                            "confidence": result.confidence,
                            "product": product_name,
                            "store": store_slug,
                        },
                    )
                    return result

                logger.debug(
                    f"{model_name} confidence too low ({result.confidence:.2f} < {threshold}), "
                    f"trying next model"
                )

            except Exception as e:
                logger.warning(
                    f"{model_name} extraction failed: {e}, trying next model",
                    extra={"product": product_name, "store": store_slug},
                )
                last_error = e
                continue

        # If all models failed or returned low confidence, return last result or raise error
        if last_result:
            logger.warning(
                f"All models below threshold, using {self.MODEL_CASCADE[-1]} anyway",
                extra={"confidence": last_result.confidence, "product": product_name},
            )
            return last_result

        # All models failed
        raise RuntimeError(f"All models failed. Last error: {last_error}")

    def _build_prompt(
        self,
        text_content: str,
        store_slug: str,
        store_hint: str,
        product_name: str | None,
    ) -> str:
        """Build extraction prompt."""
        product_context = f"Product being searched: {product_name}\n" if product_name else ""

        return f"""Extract product price information from this Swedish store page.

Store: {store_slug}
{product_context}
Store-specific parsing hints:
{store_hint}

Page content (truncated):
{text_content[:6000]}

Return a JSON object with exactly these fields:
- "price": Regular price in SEK as a number (e.g., 29.90), null if not found
- "unit_price": Price per piece/item for multi-packs. For products like toilet paper,
  diapers, etc. this should be price per roll/piece, NOT per kg. If the page shows
  an irrelevant unit price (like kr/kg for toilet paper), calculate it yourself:
  unit_price = price / pack_size. Null if not applicable.
- "pack_size": Number of items in the pack, extracted from product title patterns like
  "16-p", "24-p", "16 st", "24 st", "16-pack", "24-pack", "16 rullar". Null if not
  a multi-pack product.
- "offer_price": Discounted/campaign price if on sale, null if no discount
- "offer_type": Type of offer ("stammispris", "extrapris", "kampanj", "medlemspris"),
  null if no offer
- "offer_details": Offer description in Swedish (e.g., "Kop 3 betala for 2"), null if none
- "in_stock": boolean, true if available, false if out of stock
- "confidence": Your confidence in the extraction from 0.0 to 1.0

Only output the JSON object, no explanation or markdown."""

    async def _extract_with_model(self, prompt: str, model: str) -> PriceExtractionResult:
        """Extract using specified model via OpenRouter."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                headers=OPENROUTER_HEADERS,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        data = json.loads(content)

        # Extract values
        price = Decimal(str(data["price"])) if data.get("price") else None
        unit_price = Decimal(str(data["unit_price"])) if data.get("unit_price") else None
        pack_size = int(data["pack_size"]) if data.get("pack_size") else None

        # If we have price and pack_size but no unit_price, calculate it
        if price and pack_size and not unit_price:
            unit_price = price / pack_size

        return PriceExtractionResult(
            price_sek=price,
            unit_price_sek=unit_price,
            offer_price_sek=(
                Decimal(str(data["offer_price"])) if data.get("offer_price") else None
            ),
            offer_type=data.get("offer_type"),
            offer_details=data.get("offer_details"),
            in_stock=data.get("in_stock", True),
            confidence=float(data.get("confidence", 0.5)),
            pack_size=pack_size,
            raw_response=data,
        )
