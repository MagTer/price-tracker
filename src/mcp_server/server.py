"""FastMCP server exposing price-tracker capabilities to the agent platform.

Four tools mirror the legacy chat-tool surface:
- check_price
- find_deals
- compare_stores
- list_products

Mounted at /mcp on the FastAPI app and served on the dedicated mcp.<domain>
subdomain (IAP-bypassed, bearer-only auth).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastmcp import FastMCP

from domain.service import PriceTrackerService
from domain.tenant import DEFAULT_TENANT_ID
from infra.db import async_session_factory
from mcp_server.auth import BearerTokenMiddleware, MCP_BEARER_TOKEN

logger = logging.getLogger(__name__)

mcp = FastMCP("Price Tracker")


def _get_service() -> PriceTrackerService:
    return PriceTrackerService(async_session_factory)


def _fmt_price(val: Decimal | float | None) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):.2f} kr"


@mcp.tool()
async def check_price(product_name: str) -> str:
    """Check current prices for a product across all tracked stores.

    Args:
        product_name: Name of the product to look up (e.g. "Apotea omega-3").

    Returns:
        Markdown summary of prices and stock status per store.
    """
    service = _get_service()
    products = await service.get_products(search=product_name)

    if not products:
        return f"Inga produkter matchade \"{product_name}\"."

    lines: list[str] = [f"## Priser för: {product_name}", ""]

    for product in products:
        lines.append(f"**{product['name']}** ({product['brand'] or 'okänt märke'})")
        history = await service.get_price_history(product["id"], days=7)
        if not history:
            lines.append("- Inga priser registrerade än.")
        else:
            latest_by_store: dict[str, dict] = {}
            for row in history:
                store = row["store_name"]
                if store not in latest_by_store:
                    latest_by_store[store] = row
            for store, row in latest_by_store.items():
                price = _fmt_price(row.get("price_sek"))
                offer = _fmt_price(row.get("offer_price_sek"))
                stock = "i lager" if row.get("in_stock") else "slut i lager"
                if row.get("offer_price_sek"):
                    lines.append(f"- **{store}**: ~~{price}~~ → {offer} ({stock})")
                else:
                    lines.append(f"- **{store}**: {price} ({stock})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def find_deals(store_type: str | None = None) -> str:
    """Find current offers and discounted products.

    Args:
        store_type: Optional filter — "grocery" or "pharmacy".

    Returns:
        Markdown list of current deals sorted by recency.
    """
    service = _get_service()
    deals = await service.get_current_deals(store_type=store_type)

    if not deals:
        filter_text = f" för {store_type}" if store_type else ""
        return f"Inga aktiva erbjudanden hittades{filter_text}."

    lines: list[str] = [f"## Aktuella erbjudanden{' (' + store_type + ')' if store_type else ''}", ""]

    for deal in deals[:20]:
        regular = _fmt_price(deal.get("regular_price_sek"))
        offer = _fmt_price(deal.get("offer_price_sek"))
        lines.append(
            f"- **{deal['product_name']}** hos *{deal['store_name']}*: "
            f"~~{regular}~~ → **{offer}** "
            f"({deal.get('offer_type', 'kampanj')})"
        )

    return "\n".join(lines)


@mcp.tool()
async def compare_stores(product_name: str) -> str:
    """Compare prices for a product side-by-side across stores.

    Args:
        product_name: Name of the product to compare.

    Returns:
        Markdown table with store, price, offer, and unit price.
    """
    service = _get_service()
    products = await service.get_products(search=product_name)

    if not products:
        return f"Inga produkter matchade \"{product_name}\"."

    lines: list[str] = [f"## Jämför priser: {product_name}", ""]

    for product in products:
        lines.append(f"### {product['name']}")
        lines.append("")
        lines.append("| Butik | Pris | Erbjudande | Jämförspris | Lager |")
        lines.append("|-------|------|------------|-------------|-------|")

        history = await service.get_price_history(product["id"], days=7)
        seen_stores: set[str] = set()
        for row in history:
            store = row["store_name"]
            if store in seen_stores:
                continue
            seen_stores.add(store)
            price = _fmt_price(row.get("price_sek"))
            offer = _fmt_price(row.get("offer_price_sek"))
            unit = _fmt_price(row.get("unit_price_sek"))
            stock = "Ja" if row.get("in_stock") else "Nej"
            lines.append(f"| {store} | {price} | {offer} | {unit} | {stock} |")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def list_products() -> str:
    """List all tracked products in the inventory.

    Returns:
        Markdown list of products with categories and linked stores.
    """
    service = _get_service()
    products = await service.get_products()
    stores = await service.get_stores()
    store_map = {s["id"]: s["name"] for s in stores}

    if not products:
        return "Inga produkter är registrerade än."

    lines: list[str] = ["## Bevakade produkter", ""]

    for product in products:
        store_names = [
            store_map.get(s.get("store_id"), s.get("store_name", "?"))
            for s in (product.get("stores") or [])
        ]
        stores_str = ", ".join(store_names) if store_names else "Ej länkad"
        lines.append(
            f"- **{product['name']}** ({product['brand'] or 'okänt märke'}) — "
            f"{product['category'] or 'okategoriserad'} — *{stores_str}*"
        )

    return "\n".join(lines)


def get_mcp_app():
    """Return a `(wrapped_app, http_app)` tuple.

    `wrapped_app` is the MCP ASGI app wrapped with bearer-token auth — mount
    this on the FastAPI app. The wrap is unconditional: with no token
    configured the middleware fails closed (503 on every request) instead of
    exposing the endpoint.

    `http_app` is always the raw, unwrapped streamable-HTTP ASGI app. It is
    returned separately because Starlette's `app.mount()` does not propagate
    a mounted sub-app's own lifespan, so the caller must enter
    `http_app.lifespan(http_app)` itself to actually start/stop fastmcp's
    internal streamable-HTTP session manager.
    """
    # fastmcp >= 2.0 exposes the modern streamable-HTTP ASGI app via http_app()
    http_app = mcp.http_app()

    if not MCP_BEARER_TOKEN:
        logger.error(
            "MCP_BEARER_TOKEN not set — MCP endpoint will answer 503 until configured"
        )
    return BearerTokenMiddleware(http_app, MCP_BEARER_TOKEN), http_app
