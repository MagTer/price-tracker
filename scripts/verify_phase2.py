"""Phase 2 integration verification script.

This script verifies:
1. DB session factory works
2. Fetcher can retrieve a real Willys page
3. Service can be instantiated and check_price runs (fetcher + DB path)
4. Scheduler starts/stops correctly

Usage (from repo root, with postgres running and alembic upgraded):
    poetry run python scripts/verify_phase2.py
"""

import asyncio
import os
import sys

sys.path.insert(0, "src")

from sqlalchemy import select

from domain.models import Product, ProductStore, Store
from domain.scheduler import PriceCheckScheduler
from domain.service import PriceTrackerService
from domain.tenant import DEFAULT_TENANT_ID
from infra.db import async_session_factory
from infra.fetcher import WebFetcher
from infra.providers import get_email_service


async def main() -> int:
    print("=== Phase 2 Integration Verification ===\n")

    # 1. Verify DB connectivity and seeded stores
    async with async_session_factory() as session:
        result = await session.execute(select(Store).where(Store.slug == "willys"))
        willys = result.scalar_one_or_none()
        if willys is None:
            print("FAIL: Willys store not found in DB")
            return 1
        print(f"OK: Willys store found ({willys.id})")

    # 2. Verify fetcher can retrieve a real Willys page
    fetcher = WebFetcher()
    test_url = "https://www.willys.se/produkt/Ost-Svecia-Skivad-500g-100116615"
    print(f"Fetching {test_url} ...")
    fetch_result = await fetcher.fetch(test_url)
    if not fetch_result.get("ok"):
        print(f"FAIL: Fetch failed: {fetch_result.get('error')}")
        return 1
    text_len = len(fetch_result.get("text", ""))
    print(f"OK: Fetch succeeded ({text_len} chars extracted)")
    await fetcher.close()

    # 3. Create a test product + product_store in DB
    service = PriceTrackerService(session_factory=async_session_factory)
    async with async_session_factory() as session:
        product = Product(
            tenant_id=DEFAULT_TENANT_ID,
            name="Test Svecia Ost",
            brand="Test",
            category="Mejeri",
            unit="g",
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        print(f"OK: Created test product ({product.id})")

        product_store = ProductStore(
            product_id=product.id,
            store_id=willys.id,
            store_url=test_url,
            check_frequency_hours=72,
        )
        session.add(product_store)
        await session.commit()
        await session.refresh(product_store)
        print(f"OK: Created product_store link ({product_store.id})")

    # 4. Run service.check_price (fetcher + DB path)
    #    Without OPENROUTER_API_KEY, the parser will fail after fetch.
    print("\nRunning service.check_price (requires OPENROUTER_API_KEY for parser success)...")
    os.environ.setdefault("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    os.environ.setdefault(
        "PRICE_PARSER_MODEL_CASCADE",
        "meta-llama/llama-4-scout,anthropic/claude-haiku-4.5",
    )

    result = await service.check_price(str(product_store.id))
    if result.get("error"):
        print(f"WARN: check_price returned error: {result['error']}")
        print("      (Expected if OPENROUTER_API_KEY is not set)")
    else:
        print(f"OK: check_price succeeded, price={result.get('price_sek')} SEK")

    # 5. Verify scheduler can be instantiated and started/stopped
    scheduler = PriceCheckScheduler(
        session_factory=async_session_factory,
        fetcher=WebFetcher(),
        email_service=get_email_service(),
    )
    await scheduler.start()
    status = scheduler.get_status()
    if not status.get("running"):
        print("FAIL: Scheduler did not start")
        return 1
    print(f"OK: Scheduler started (running={status['running']})")
    await scheduler.stop()
    print("OK: Scheduler stopped")

    # 6. Cleanup test data
    async with async_session_factory() as session:
        await session.delete(product)
        await session.commit()
        print("OK: Cleaned up test product")

    print("\n=== Phase 2 Verification Complete ===")
    print("NOTE: Full end-to-end price persistence requires OPENROUTER_API_KEY.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
