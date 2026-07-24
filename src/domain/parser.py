"""LLM-based price extraction with cost optimization."""

import dataclasses
import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from html import unescape

import httpx

from domain.extractors.base import PriceExtractor
from domain.extractors.jsonld import JsonLdExtractor
from domain.extractors.willys_api import WillysApiExtractor
from domain.result import PriceExtractionResult, ProductMetadata
from infra.llm import OPENROUTER_BASE_URL, OPENROUTER_HEADERS

__all__ = ["PriceExtractionResult", "PriceParser", "ProductMetadata"]

logger = logging.getLogger(__name__)

# Grabs the first numeric run, tolerating spaces/NBSP as thousands separators and both
# ',' and '.' as decimal marks: "32,90 kr", "1 234,50", "29.90 SEK", "0,5 l" all match.
_NUMERIC_RE = re.compile(r"-?\d[\d\s .,]*")


def _to_decimal(value: object) -> Decimal | None:
    """Coerce an LLM's price/amount field to Decimal, or None if there is no number.

    LLMs ignore "as a number" and return "32,90 kr" (Swedish comma + currency) as often
    as 32.90 — a bare Decimal(str(value)) on that raises decimal.ConversionSyntax, which
    is exactly what killed the old llama-4-scout price path. This normalizes ONE number
    out of the value (currency words/symbols stripped, Swedish comma → dot, thousands
    separators removed) so any model's output survives. Never raises: garbage → None,
    and the caller's confidence floor / null handling takes over from there.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — a price is never a boolean
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    match = _NUMERIC_RE.search(str(value))
    if match is None:
        return None
    # Drop ALL whitespace (ASCII space, NBSP, tabs) — any of them can be a thousands sep.
    token = re.sub(r"\s", "", match.group(0))
    if "," in token and "." in token:
        # Both present: the LAST separator is the decimal mark, the other is thousands.
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        token = token.replace(",", ".")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _to_int(value: object) -> int | None:
    """Coerce an LLM's pack-size field to int, or None. Tolerates "24-pack", "24 st"."""
    dec = _to_decimal(value)
    if dec is None:
        return None
    try:
        return int(dec)
    except (ValueError, OverflowError):
        return None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_TAG_RE = re.compile(r"<meta\b[^>]*?>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*("[^"]*"|'[^']*')""")
_LDJSON_RE = re.compile(
    r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
# The identity signals of a page live in its <head> and <script> tags, which the
# fetcher's visible-text extraction strips out. For a JS SPA (ICA, Willys) that leaves
# the LLM fallback with almost nothing — it returns all-nulls on a real product. So when
# the structured JSON-LD parse has ALREADY failed (that is the only time this fallback
# runs), lead the LLM with the head/JSON-LD signals it can still read.
_LDJSON_BUDGET = 3000


def _clean_signal(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _meta_attrs(tag: str) -> dict[str, str]:
    return {k.lower(): v[1:-1] for k, v in _ATTR_RE.findall(tag)}


def _build_metadata_context(html_content: str, fallback_text: str, *, budget: int = 6000) -> str:
    """Assemble the richest possible LLM context from raw HTML + stripped visible text.

    Leads with <title>, the description/og meta tags, and any raw JSON-LD blocks (the
    exact data a SPA hides from visible text), then appends the visible text. Truncated
    to `budget`. If the HTML carries no signals (e.g. a blocked/empty page), this is just
    the visible text — no regression over the old text-only behaviour.
    """
    parts: list[str] = []

    title_match = _TITLE_RE.search(html_content)
    if title_match:
        title = _clean_signal(title_match.group(1))
        if title:
            parts.append(f"Title: {title}")

    seen: set[str] = set()
    for tag in _META_TAG_RE.findall(html_content):
        attrs = _meta_attrs(tag)
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        content = attrs.get("content")
        if key in ("description", "og:title", "og:description") and content and key not in seen:
            seen.add(key)
            parts.append(f"{key}: {_clean_signal(content)}")

    blocks = [b.strip() for b in _LDJSON_RE.findall(html_content) if b.strip()]
    if blocks:
        parts.append("Structured data (JSON-LD):\n" + "\n".join(blocks)[:_LDJSON_BUDGET])

    head = "\n".join(parts)
    if head and fallback_text:
        context = f"{head}\n\nVisible text:\n{fallback_text}"
    else:
        context = head or fallback_text
    return context[:budget]


class PriceParser:
    """LLM-based price extractor with cascading model strategy."""

    # Cascading model strategy (fast/cheap first, then a cross-vendor fallback). Both
    # defaults are served by the account's allowed OpenRouter providers (DeepInfra/Novita/
    # Parasail) — the previous default fell back to anthropic/claude-haiku-4.5, which 404s
    # under that provider allowlist ("no allowed providers"), so quick-add's metadata path
    # had no working model at all. Override wholesale via PRICE_PARSER_MODEL_CASCADE.
    MODEL_CASCADE = os.getenv(
        "PRICE_PARSER_MODEL_CASCADE",
        "deepseek/deepseek-v4-flash,meta-llama/llama-4-scout",
    ).split(",")

    # Acceptance floor: extractions below this confidence are discarded rather
    # than recorded — a gap in price history beats a hallucinated price point.
    MIN_ACCEPT_CONFIDENCE = float(os.getenv("PRICE_PARSER_MIN_CONFIDENCE", "0.6"))

    # Model-specific confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        "deepseek/deepseek-v4-flash": 0.70,
        "meta-llama/llama-4-scout": MIN_ACCEPT_CONFIDENCE,  # Last resort still honors the floor
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

    async def extract_product_metadata(
        self, text_content: str, store_slug: str, *, html_content: str | None = None
    ) -> ProductMetadata | None:
        """Identify the product on a page (name/brand/category/package) for quick-add.

        The LLM fallback for pages without a usable JSON-LD Product node. Same cascade
        order and acceptance floor as price extraction; returns None instead of raising —
        quick-add degrades to asking the user to type the name, it never errors out on
        an extraction failure.

        When `html_content` is given, the prompt is built from the page's identity signals
        (title, meta, raw JSON-LD) plus the visible text rather than the visible text alone
        — the difference between the LLM seeing a product and seeing an empty SPA shell.
        """
        page = (
            _build_metadata_context(html_content, text_content)
            if html_content
            else text_content[:6000]
        )
        prompt = f"""Identify the product being sold on this Swedish store page.

Store: {store_slug}

Page content (truncated):
{page}

Return a JSON object with exactly these fields:
- "name": The product's name WITHOUT package size suffixes (e.g. "Lambi Toalettpapper 3-lager",
  not "Lambi Toalettpapper 3-lager 24-pack"). Null if you cannot tell.
- "brand": The brand name (e.g. "Lambi"), null if not clear.
- "category": A short product category in Swedish, one or two words
  (e.g. "toalettpapper", "tandkräm", "kosttillskott"). Null if unclear.
- "price": Current price in SEK as a number, null if not found.
- "pack_size": Number of items in the pack from patterns like "24-pack", "16 st". Null if
  not a multi-pack product.
- "package_amount": The numeric amount of product in the package, as printed
  (e.g. 0.5 for "0,5 l", 400 for "400 g", 24 for "24-pack"). Null if not stated.
- "package_unit": The unit that amount is expressed in - one of "st", "ml", "l",
  "g", "kg". Null if not stated.
- "confidence": Your confidence in the identification from 0.0 to 1.0.

Only output the JSON object, no explanation or markdown."""

        for model_name in self.MODEL_CASCADE:
            try:
                data = await self._call_model_json(prompt, model_name)
                confidence = float(data.get("confidence", 0.5))
                if confidence < self.MIN_ACCEPT_CONFIDENCE:
                    logger.debug(
                        f"{model_name} metadata confidence too low ({confidence:.2f}), "
                        f"trying next model"
                    )
                    continue
                name = str(data["name"]).strip() if data.get("name") else None
                return ProductMetadata(
                    name=name,
                    brand=str(data["brand"]).strip() if data.get("brand") else None,
                    category=str(data["category"]).strip() if data.get("category") else None,
                    price_sek=_to_decimal(data.get("price")),
                    package_amount=_to_decimal(data.get("package_amount")),
                    package_unit=(
                        str(data["package_unit"]).strip().lower()
                        if data.get("package_unit")
                        else None
                    ),
                    pack_size=_to_int(data.get("pack_size")),
                    confidence=confidence,
                    source="llm",
                )
            except Exception as e:
                logger.warning(
                    f"{model_name} metadata extraction failed: {e}, trying next model",
                    extra={"store": store_slug},
                )
                continue

        logger.warning(
            "Product metadata extraction failed on all models", extra={"store": store_slug}
        )
        return None

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

    async def _call_model_json(self, prompt: str, model: str) -> dict:
        """One OpenRouter chat call, response parsed as a JSON object."""
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

        return json.loads(content)

    async def _extract_with_model(self, prompt: str, model: str) -> PriceExtractionResult:
        """Extract using specified model via OpenRouter."""
        data = await self._call_model_json(prompt, model)

        # Extract values. The unit price is REPORTED, never synthesized: this field carries
        # only what the store printed (D-05), so a page/store mismatch stays detectable
        # instead of being papered over by our own arithmetic. When the page prints nothing
        # it stays None — the computed unit price covers that case.
        price = _to_decimal(data.get("price"))
        store_unit_price = _to_decimal(data.get("unit_price"))
        pack_size = _to_int(data.get("pack_size"))
        package_amount = _to_decimal(data.get("package_amount"))
        package_unit = (
            str(data["package_unit"]).strip().lower() if data.get("package_unit") else None
        )

        return PriceExtractionResult(
            price_sek=price,
            store_unit_price_sek=store_unit_price,
            offer_price_sek=_to_decimal(data.get("offer_price")),
            offer_type=data.get("offer_type"),
            offer_details=data.get("offer_details"),
            in_stock=data.get("in_stock", True),
            confidence=float(data.get("confidence", 0.5)),
            pack_size=pack_size,
            package_amount=package_amount,
            package_unit=package_unit,
            raw_response=data,
        )
