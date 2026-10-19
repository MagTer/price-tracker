"""THE price-extraction ladder: store API, then store page-state, then JSON-LD — and nothing after.

Every tier reads structure the STORE published. There is deliberately no language-model tier
(removed 2026-10-19, v0.66.0): over the tracker's whole prod history the LLM fallback answered
8 checks, all on two links whose product no longer existed at the store, and all 8 points were
wrong — an inverted ordinarie, an EU-Omnibus disclosure stored as villkor, and a price that
appears nowhere on the page. Because it always answered, those checks reported "ok" and
link_health never flagged the links. A page no structured tier can read is now an honest
no_price with a reason; a store stating the product is gone (ProductGoneError) ends the ladder.
The JSON-LD enrichment pass went with it, on the same review: its offers were JSON-LD's own
("Spara 39 kr" is the jsonld extractor's wording), leaving a jämförpris on a handful of points
as its only contribution. Re-adding a model here needs a measurement that it adds value.
"""

import logging

from domain.extractors.base import HtmlPriceExtractor, PriceExtractor
from domain.extractors.clasohlson import ClasOhlsonExtractor
from domain.extractors.ica import IcaExtractor
from domain.extractors.jsonld import JsonLdExtractor
from domain.extractors.jysk import JyskExtractor
from domain.extractors.lyko import LykoExtractor
from domain.extractors.rusta import RustaExtractor
from domain.extractors.willys_api import WillysApiExtractor
from domain.result import PriceExtractionResult, ProductGoneError, StoreBlockedError

__all__ = ["PriceExtractionResult", "PriceParser"]

logger = logging.getLogger(__name__)


# THE registry of store-API extractors — structured store endpoints that beat HTML scraping.
# Shared by the price-check ladder (PriceParser) AND quick-add preview (api/admin.py) so a
# store with a real API is read the same way everywhere. One definition; do not make a second.
_API_EXTRACTORS: dict[str, WillysApiExtractor] = {"willys": WillysApiExtractor()}

# THE registry of store-HTML extractors — stores whose PAGE carries richer structure than
# their JSON-LD (Rusta and Lyko: window.CURRENT_PAGE hydration state; ICA: the
# __QUERY_INITIAL_STATE__ react-query state, whose promotions and jämförpris the JSON-LD
# never carries; Clas Ohlson and JYSK: the price markup — Rusta/Lyko/Clas Ohlson/JYSK hide
# the ordinarie at a rea from JSON-LD, which carries only the current price, and JYSK also
# publishes multi-variant products as a ProductGroup the generic Product walk never finds).
# Same sharing
# contract as _API_EXTRACTORS, one tier further down the ladder: these parse the
# already-fetched page and never make an HTTP call of their own, which is why they need
# no ledger slot and can never raise StoreBlockedError.
_HTML_EXTRACTORS: dict[str, HtmlPriceExtractor] = {
    "rusta": RustaExtractor(),
    "clasohlson": ClasOhlsonExtractor(),
    "lyko": LykoExtractor(),
    "ica": IcaExtractor(),
    "jysk": JyskExtractor(),
}


def get_api_extractor(store_slug: str) -> WillysApiExtractor | None:
    """The store-API extractor for this slug, or None if the store has no structured API."""
    return _API_EXTRACTORS.get(store_slug)


def get_html_extractor(store_slug: str) -> HtmlPriceExtractor | None:
    """The store-HTML extractor for this slug, or None if generic JSON-LD is the best tier."""
    return _HTML_EXTRACTORS.get(store_slug)


class PriceParser:
    """Runs the structured extraction ladder over one fetched page."""

    def __init__(self) -> None:
        # A working COPY of the shared registries: the module-level dicts stay the one
        # canonical list (get_api_extractor/get_html_extractor read them), while a test
        # swapping in a mock on an instance cannot leak into every other consumer.
        self._api_extractors: dict[str, PriceExtractor] = dict(_API_EXTRACTORS)
        self._html_extractors: dict[str, HtmlPriceExtractor] = dict(_HTML_EXTRACTORS)
        self._jsonld_extractor = JsonLdExtractor()

    @staticmethod
    def _no_price(source: str, reason: str) -> PriceExtractionResult:
        """The ladder's honest end: no price, the tier that ended it, and why.

        ``reason`` becomes check_attempts.detail, which link_health hands Fel & luckor as
        the broken link's "senaste svar" — so it is written for the reader of that page.
        """
        return PriceExtractionResult(
            price_sek=None,
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.0,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={"source": source, "reason": reason},
        )

    @staticmethod
    def _gone(e: ProductGoneError, store_slug: str) -> PriceExtractionResult:
        logger.warning("Product gone at %s: %s - ending the ladder", store_slug, e)
        return PriceParser._no_price("product_gone", f"produkten finns inte hos butiken ({e})")

    async def extract_price(
        self,
        text_content: str,
        store_slug: str,
        product_name: str | None = None,
        store_url: str | None = None,
        html_content: str | None = None,
    ) -> PriceExtractionResult:
        """Extract price data: store API, then store page-state, then JSON-LD.

        Always returns a result; ``price_sek is None`` means no tier could read the page,
        and ``raw_response["reason"]`` says why. Raises only StoreBlockedError (a wall).
        """
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
                    "API extractor returned None, trying the page", extra={"store": store_slug}
                )
            except StoreBlockedError:
                # The HOST is walling us — stop the ladder. JSON-LD on a Willys SPA shell
                # finds nothing, and the caller must see a BLOCK (perform_price_check turns
                # this into a blocked outcome for the circuit breaker), not a routine
                # "no price on the page".
                raise
            except ProductGoneError as e:
                return self._gone(e, store_slug)
            except Exception as e:
                logger.warning(
                    "API extractor failed, trying the page: %s", e, extra={"store": store_slug}
                )

        # Store-HTML tier: a store whose page embeds richer structured state than its
        # JSON-LD. For Rusta this tier is what keeps a rea honest — the JSON-LD carries only
        # the current price, so falling through would record the campaign price as ordinarie.
        if html_content and store_url and store_slug in self._html_extractors:
            try:
                page_result = self._html_extractors[store_slug].extract_from_html(
                    html_content, store_url, product_name
                )
                if page_result is not None:
                    logger.info(
                        "Price extracted via page state",
                        extra={
                            "method": "page_state",
                            "store": store_slug,
                            "product": product_name,
                        },
                    )
                    return page_result
                logger.debug(
                    "Page-state extractor returned None, falling back to JSON-LD",
                    extra={"store": store_slug},
                )
            except ProductGoneError as e:
                return self._gone(e, store_slug)
            except Exception as e:
                logger.warning(
                    "Page-state extractor failed, falling back to JSON-LD: %s",
                    e,
                    extra={"store": store_slug},
                )

        # JSON-LD last: exact structured data from the already-fetched page
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
            except Exception as e:
                logger.warning("JSON-LD extraction failed: %s", e, extra={"store": store_slug})

        # Every structured tier declined. The WARNING is the point of detection: a store whose
        # page changed shape shows up here and as a no_price streak in check_attempts — which
        # is what link_health and the tier-regression alarm are built to surface.
        logger.warning(
            "No structured price on %s (%s) - recording no price",
            store_url or "page",
            store_slug,
            extra={"store": store_slug, "product": product_name},
        )
        return self._no_price("none", "sidan saknar läsbar prisuppgift")
