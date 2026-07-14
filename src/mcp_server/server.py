"""FastMCP server exposing price-tracker capabilities to the agent platform.

Four tools mirror the legacy chat-tool surface:
- check_price
- find_deals
- compare_stores
- list_products

Mounted at /mcp on the FastAPI app. The ingress bypasses the Entra gate for
exactly this path (per-router forwardAuth bypass, D-29 — supersedes the
D-18 mcp.<domain> subdomain plan); auth here is bearer-only, fail-closed.
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
    """Check the latest observed price for a product on every tracked store listing.

    A quick "what does it cost where" summary. For "which package is cheapest per
    unit", use compare_stores instead.

    Args:
        product_name: Name of the product to look up (e.g. "Apotea omega-3").

    Returns:
        Markdown summary of the latest price and stock status per store listing.
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
            # Deduped per LINK, not per store name: a product may now hold several
            # listings at one store (different pack sizes), and keying on the store
            # name would collapse them into an arbitrary winner. History is ordered
            # checked_at DESC, so the first row seen per link is the latest.
            latest_by_link: dict[str, dict] = {}
            for row in history:
                key = str(row.get("product_store_id") or row["store_name"])
                if key not in latest_by_link:
                    latest_by_link[key] = row
            for row in latest_by_link.values():
                store = row["store_name"]
                package = row.get("package_size")
                label = f"{store} ({package})" if package else store
                price = _fmt_price(row.get("price_sek"))
                offer = _fmt_price(row.get("offer_price_sek"))
                stock = "i lager" if row.get("in_stock") else "slut i lager"
                if row.get("offer_price_sek"):
                    lines.append(f"- **{label}**: ~~{price}~~ → {offer} ({stock})")
                else:
                    lines.append(f"- **{label}**: {price} ({stock})")
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
    """Compare every tracked package of a product, ranked cheapest per unit.

    One row per store listing — so two pack sizes at the same store are two rows,
    which is the whole point: the cheapest package is not always the cheapest one
    per roll/liter/kg. Rows come back already ranked by the COMPUTED comparison
    price (price ÷ the package's amount), cheapest first; listings whose amount is
    not yet known sort last and say so.

    "Jämförspris" is that computed value and is the only comparable one. "Butiken
    anger" is what the store itself printed — stores use different definitions
    (kr/rulle at one, kr/100g at another), so it is shown as a raw signal and must
    never be used to rank.

    Args:
        product_name: Name of the product to compare.

    Returns:
        Markdown table per product: store, package, price, offer, computed
        comparison price, the store's printed comparison price, and stock.
    """
    service = _get_service()
    products = await service.get_products(search=product_name)

    if not products:
        return f"Inga produkter matchade \"{product_name}\"."

    lines: list[str] = [f"## Jämför priser: {product_name}", ""]

    for product in products:
        lines.append(f"### {product['name']}")
        lines.append("")

        # Already one row per link and already ranked cheapest-per-unit with NULL
        # quantities last (D-13). This tool is a render: no sorting and no unit-price
        # arithmetic happens here — the domain owns the one definition (D-03).
        links = await service.get_links_for_product(product["id"])

        if not links:
            lines.append("Inga butikslänkar registrerade än.")
            lines.append("")
            continue

        unit = product.get("unit") or "enhet"
        lines.append(
            f"| Butik | Förpackning | Pris | Erbjudande | Jämförspris (kr/{unit}) "
            f"| Butiken anger | Lager |"
        )
        lines.append(
            "|-------|-------------|------|------------|--------------|---------------|-------|"
        )

        for link in links:
            store = link.get("store_name") or "?"
            package = link.get("package_size") or "—"
            price = _fmt_price(link.get("price_sek"))
            offer = _fmt_price(link.get("offer_price_sek"))
            # D-02: a link may legitimately have no amount yet (the first scrape autofills
            # it). Say so explicitly — a bare "N/A" reads as broken, "saknar mängd" tells
            # the operator exactly what to fix. These rows already sort last.
            if link.get("package_quantity") is None:
                unit_price = "saknar mängd"
            else:
                unit_price = _fmt_price(link.get("unit_price_sek"))
            # D-05: the store's PRINTED value, its own column, never the sort key.
            store_says = _fmt_price(link.get("store_unit_price_sek"))
            in_stock = link.get("in_stock")
            stock = "?" if in_stock is None else ("Ja" if in_stock else "Nej")
            lines.append(
                f"| {store} | {package} | {price} | {offer} | {unit_price} "
                f"| {store_says} | {stock} |"
            )
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
    # fastmcp >= 2.0 exposes the modern streamable-HTTP ASGI app via http_app().
    # path="/" because the FastAPI mount already provides the /mcp prefix —
    # fastmcp's default (path="/mcp") would double it to /mcp/mcp/.
    http_app = mcp.http_app(path="/")

    if not MCP_BEARER_TOKEN:
        logger.error(
            "MCP_BEARER_TOKEN not set — MCP endpoint will answer 503 until configured"
        )
    return BearerTokenMiddleware(http_app, MCP_BEARER_TOKEN), http_app
