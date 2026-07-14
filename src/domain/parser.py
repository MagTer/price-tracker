"""LLM-based price extraction with cost optimization."""

import dataclasses
import json
import logging
import os
from decimal import Decimal

import httpx

from domain.extractors.base import PriceExtractor
from domain.extractors.jsonld import JsonLdExtractor
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
        "meta-llama/llama-4-scout,anthropic/claude-haiku-4.5",
    ).split(",")

    # Acceptance floor: extractions below this confidence are discarded rather
    # than recorded — a gap in price history beats a hallucinated price point.
    MIN_ACCEPT_CONFIDENCE = float(os.getenv("PRICE_PARSER_MIN_CONFIDENCE", "0.6"))

    # Model-specific confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        "meta-llama/llama-4-scout": 0.70,
        "anthropic/claude-haiku-4.5": MIN_ACCEPT_CONFIDENCE,  # Last resort still honors the floor
    }

    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self) -> None:
        self._store_hints: dict[str, str] = {}
        self._load_store_hints()
        self._api_extractors: dict[str, PriceExtractor] = {
            "willys": WillysApiExtractor(),
        }
        self._jsonld_extractor = JsonLdExtractor()

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
        html_content: str | None = None,
    ) -> PriceExtractionResult:
        """Extract price data: store API first, then JSON-LD, then LLM cascade."""

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

        # JSON-LD second: exact structured data from the already-fetched page
        if html_content:
            try:
                jsonld_result = self._jsonld_extractor.extract_from_html(html_content, product_name)
                if jsonld_result is not None:
                    logger.info(
                        "Price extracted via JSON-LD",
                        extra={
                            "method": "jsonld",
                            "store": store_slug,
                            "product": product_name,
                        },
                    )
                    return jsonld_result
                logger.debug(
                    "No usable JSON-LD Product, falling back to LLM",
                    extra={"store": store_slug},
                )
            except Exception as e:
                logger.warning(
                    "JSON-LD extraction failed, falling back to LLM: %s",
                    e,
                    extra={"store": store_slug},
                )

        store_hint = self._store_hints.get(store_slug, "")

        prompt = self._build_prompt(text_content, store_slug, store_hint, product_name)

        return await self._run_llm_cascade(prompt, product_name=product_name, store_slug=store_slug)

    async def _run_llm_cascade(
        self, prompt: str, *, product_name: str | None, store_slug: str
    ) -> PriceExtractionResult:
        """THE model cascade — the only place it exists (extract_price and enrich_with_llm
        both route through here).

        Returns the accepted result, or a discarded (price_sek=None) result when the best
        confidence is below the acceptance floor. Raises RuntimeError only when every model
        raised — callers that must never fail (enrich_with_llm) catch that themselves.
        """
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
            if last_result.confidence >= self.MIN_ACCEPT_CONFIDENCE:
                logger.warning(
                    f"All models below threshold, using {self.MODEL_CASCADE[-1]} anyway",
                    extra={"confidence": last_result.confidence, "product": product_name},
                )
                return last_result
            # Below the acceptance floor: discard the price so callers skip
            # recording, instead of storing a likely-hallucinated value.
            logger.warning(
                f"Best confidence {last_result.confidence:.2f} below acceptance floor "
                f"{self.MIN_ACCEPT_CONFIDENCE:.2f} - discarding extraction",
                extra={"product": product_name, "store": store_slug},
            )
            return PriceExtractionResult(
                price_sek=None,
                store_unit_price_sek=None,
                offer_price_sek=None,
                offer_type=None,
                offer_details=None,
                in_stock=last_result.in_stock,
                confidence=last_result.confidence,
                pack_size=None,
                package_amount=None,
                package_unit=None,
                raw_response={
                    "source": "discarded_low_confidence",
                    "confidence": last_result.confidence,
                },
            )

        # All models failed
        raise RuntimeError(f"All models failed. Last error: {last_error}")

    # Fields the LLM may CONTRIBUTE during enrichment — only where the base result has
    # None. Deliberately excludes price_sek and in_stock: the JSON-LD extraction stays
    # the price authority, the LLM only fills what structured data cannot carry.
    _ENRICHABLE_FIELDS = (
        "store_unit_price_sek",
        "offer_price_sek",
        "offer_type",
        "offer_details",
        "pack_size",
        "package_amount",
        "package_unit",
    )

    async def enrich_with_llm(
        self,
        base: PriceExtractionResult,
        *,
        text_content: str,
        store_slug: str,
        product_name: str | None,
    ) -> PriceExtractionResult:
        """Fill offer/package fields a structured extraction lacks, from the SAME page.

        Reuses the already-fetched text — enrichment never fetches a page. Never raises:
        any cascade failure or below-floor confidence returns `base` unchanged, so an
        enrichment problem can never fail a check whose price is already in hand.
        """
        try:
            store_hint = self._store_hints.get(store_slug, "")
            prompt = self._build_prompt(text_content, store_slug, store_hint, product_name)
            result = await self._run_llm_cascade(
                prompt, product_name=product_name, store_slug=store_slug
            )
        except Exception as e:
            logger.warning(
                f"LLM enrichment failed, keeping base extraction: {e}",
                extra={"product": product_name, "store": store_slug},
            )
            return base

        if result is None or result.confidence < self.MIN_ACCEPT_CONFIDENCE:
            logger.warning(
                "LLM enrichment below acceptance floor - keeping base extraction",
                extra={"product": product_name, "store": store_slug},
            )
            return base

        updates: dict[str, object] = {
            field: getattr(result, field)
            for field in self._ENRICHABLE_FIELDS
            if getattr(base, field) is None and getattr(result, field) is not None
        }

        # Keep the base raw_response (its "source" stays truthful — an enriched
        # JSON-LD check still counts as jsonld in the scheduler stats) and mark
        # the enrichment on top.
        raw = dict(base.raw_response or {})
        raw["enriched"] = True
        raw["enrichment_confidence"] = result.confidence
        updates["raw_response"] = raw

        return dataclasses.replace(base, **updates)

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
- "unit_price": The comparison price (jamforpris) EXACTLY as printed on the page,
  including when its unit differs from the pack unit (e.g. kr/kg printed for toilet
  paper). Do NOT compute it. Null if the page prints none.
- "pack_size": Number of items in the pack, extracted from product title patterns like
  "16-p", "24-p", "16 st", "24 st", "16-pack", "24-pack", "16 rullar". Null if not
  a multi-pack product.
- "package_amount": The numeric amount of product in the package, as printed
  (e.g. 0.5 for "0,5 l", 400 for "400 g", 24 for "24-pack"). Null if not stated.
- "package_unit": The unit that amount is expressed in - one of "st", "ml", "l",
  "g", "kg". Null if not stated.
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

        # Extract values. The unit price is REPORTED, never synthesized: this field carries
        # only what the store printed (D-05), so a page/store mismatch stays detectable
        # instead of being papered over by our own arithmetic. When the page prints nothing
        # it stays None — the computed unit price covers that case.
        price = Decimal(str(data["price"])) if data.get("price") else None
        store_unit_price = Decimal(str(data["unit_price"])) if data.get("unit_price") else None
        pack_size = int(data["pack_size"]) if data.get("pack_size") else None
        package_amount = (
            Decimal(str(data["package_amount"])) if data.get("package_amount") else None
        )
        package_unit = (
            str(data["package_unit"]).strip().lower() if data.get("package_unit") else None
        )

        return PriceExtractionResult(
            price_sek=price,
            store_unit_price_sek=store_unit_price,
            offer_price_sek=(
                Decimal(str(data["offer_price"])) if data.get("offer_price") else None
            ),
            offer_type=data.get("offer_type"),
            offer_details=data.get("offer_details"),
            in_stock=data.get("in_stock", True),
            confidence=float(data.get("confidence", 0.5)),
            pack_size=pack_size,
            package_amount=package_amount,
            package_unit=package_unit,
            raw_response=data,
        )
