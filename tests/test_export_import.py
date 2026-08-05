"""Export/import proven as a BACKUP, against a real Postgres.

These endpoints had no test at all, which is how the export drifted into exporting nothing:
it inner-joined active watches, so a tracker with products but no watch produced an empty
`products` list under a 200 OK. A mock cannot catch that — the bug lives in the SQL. So the
claims here are made against a real database, end to end through the real endpoints.

The load-bearing property is a ROUND TRIP: export, wipe, import, and everything is back.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.admin import get_db as admin_get_db
from api.app import create_app
from api.auth import Principal, get_principal
from domain.models import PricePoint, PriceWatch, Product, ProductStore, Store
from domain.tenant import DEFAULT_TENANT_ID

pytestmark = pytest.mark.integration

_MARKER = "ExportRoundTrip"


def _now() -> datetime:
    """Naive UTC — checked_at is TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def client(session_factory: async_sessionmaker[AsyncSession]):
    """The real app, with a real DB session per request.

    A fresh session per request (rather than sharing the test's) keeps the endpoint's own
    commit/rollback semantics intact — the import's rollback path is part of what is tested.
    """
    app = create_app()

    # get_principal is THE identity point — the router's write gate resolves through it too.
    # Import is a POST, so this fixture has to be the admin or the round trip 403s.
    async def override_auth() -> Principal:
        return Principal(email="test@example.com", is_admin=True)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_principal] = override_auth
    app.dependency_overrides[admin_get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed(session: AsyncSession) -> tuple[Product, ProductStore, datetime]:
    """One product WITHOUT a watch, one per-butik link, one price point.

    No watch on purpose: that is precisely the shape the old export dropped on the floor.
    """
    store = (await session.execute(select(Store).where(Store.slug == "ica"))).scalar_one()

    product = Product(
        tenant_id=DEFAULT_TENANT_ID,
        name=f"{_MARKER} Toalettpapper",
        brand="Lambi",
        category="Skafferi",
        unit="st",
    )
    session.add(product)
    await session.flush()

    link = ProductStore(
        product_id=product.id,
        store_id=store.id,
        store_url=f"https://handlaprivatkund.ica.se/stores/1004247/products/{uuid.uuid4()}",
        store_label="ICA Maxi Sandviken",
        package_size="24-pack",
        package_quantity=Decimal("24"),
        scraped_package_quantity=Decimal("24"),
        is_active=True,
    )
    session.add(link)
    await session.flush()

    checked_at = _now() - timedelta(days=1)
    session.add(
        PricePoint(
            product_store_id=link.id,
            price_sek=Decimal("108.95"),
            store_unit_price_sek=Decimal("4.54"),
            offer_price_sek=Decimal("89.90"),
            offer_type="kampanj",
            offer_details="Spara 19.05 kr",
            in_stock=True,
            checked_at=checked_at,
        )
    )
    await session.commit()
    return product, link, checked_at


async def _wipe(session: AsyncSession, product_id: uuid.UUID) -> None:
    """Delete exactly the seeded rows, so the restore has something to prove."""
    link_stmt = select(ProductStore.id).where(ProductStore.product_id == product_id)
    link_ids = (await session.execute(link_stmt)).scalars().all()
    if link_ids:
        await session.execute(delete(PricePoint).where(PricePoint.product_store_id.in_(link_ids)))
    await session.execute(delete(PriceWatch).where(PriceWatch.product_id == product_id))
    await session.execute(delete(ProductStore).where(ProductStore.product_id == product_id))
    await session.execute(delete(Product).where(Product.id == product_id))
    await session.commit()


def _only_mine(payload: dict, product_name: str, store_url: str) -> dict:
    """Narrow an export to the seeded product, so other tests' rows cannot affect the result."""
    return {
        **payload,
        "products": [p for p in payload["products"] if p["name"] == product_name],
        "price_history": [h for h in payload["price_history"] if h.get("store_url") == store_url],
    }


class TestExport:
    async def test_exports_products_without_a_watch(self, client, db_session) -> None:
        """THE regression: a product with no watch must still be in the backup.

        The export used to inner-join active watches, so this product — and in a tracker with
        no watches at all, every product — vanished from the export while it still returned
        200 OK. A backup that silently backs up nothing is worse than no backup.
        """
        product, link, _ = await _seed(db_session)
        try:
            async with client as c:
                r = await c.get("/export")
            assert r.status_code == 200
            names = [p["name"] for p in r.json()["products"]]
            assert product.name in names
        finally:
            await _wipe(db_session, product.id)

    async def test_link_carries_store_label_and_package_data(self, client, db_session) -> None:
        """store_label is the per-butik display name; without it a restore collapses two
        butiker of one chain into the bare chain name."""
        product, link, _ = await _seed(db_session)
        try:
            async with client as c:
                r = await c.get("/export")
            exported = next(p for p in r.json()["products"] if p["name"] == product.name)
            exported_link = exported["store_links"][0]
            assert exported_link["store_label"] == "ICA Maxi Sandviken"
            assert exported_link["store_url"] == link.store_url
            assert exported_link["package_size"] == "24-pack"
            assert exported_link["package_quantity"] == pytest.approx(24.0)
            assert exported_link["scraped_package_quantity"] == pytest.approx(24.0)
        finally:
            await _wipe(db_session, product.id)

    async def test_history_rows_key_on_store_url(self, client, db_session) -> None:
        """A product may hold several links at ONE store, so product+slug cannot address a
        reading. store_url — the link's natural key — can."""
        product, link, _ = await _seed(db_session)
        try:
            async with client as c:
                r = await c.get("/export", params={"include_history": True})
            rows = [h for h in r.json()["price_history"] if h.get("store_url") == link.store_url]
            assert len(rows) == 1
            assert rows[0]["price_sek"] == pytest.approx(108.95)
            assert rows[0]["offer_price_sek"] == pytest.approx(89.90)
        finally:
            await _wipe(db_session, product.id)

    async def test_history_days_zero_reaches_past_the_default_window(
        self, client, db_session
    ) -> None:
        """history_days=0 means ALL history — the portal's backup button passes it.

        The default window is 30 days, so a backup taken with it silently drops everything
        older. This proves an old reading still lands in the file.
        """
        product, link, _ = await _seed(db_session)
        ancient = _now() - timedelta(days=200)
        db_session.add(
            PricePoint(
                product_store_id=link.id,
                price_sek=Decimal("99.00"),
                in_stock=True,
                checked_at=ancient,
            )
        )
        await db_session.commit()
        try:
            async with client as c:
                default_window = await c.get("/export", params={"include_history": True})
                everything = await c.get(
                    "/export", params={"include_history": True, "history_days": 0}
                )

            def prices(resp):
                return [
                    h["price_sek"]
                    for h in resp.json()["price_history"]
                    if h.get("store_url") == link.store_url
                ]

            assert 99.00 not in prices(default_window)  # the 30-day default drops it
            assert 99.00 in prices(everything)  # the backup keeps it
        finally:
            await _wipe(db_session, product.id)


class TestRoundTrip:
    async def test_export_wipe_import_restores_everything(self, client, db_session) -> None:
        """The whole point of a backup: what comes back must be what went in."""
        product, link, checked_at = await _seed(db_session)
        product_name, store_url = product.name, link.store_url
        try:
            async with client as c:
                r = await c.get("/export", params={"include_history": True})
                payload = _only_mine(r.json(), product_name, store_url)

                await _wipe(db_session, product.id)
                assert (
                    await db_session.execute(select(Product).where(Product.name == product_name))
                ).scalar_one_or_none() is None

                files = {"file": ("backup.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text
            summary = r.json()["summary"]
            assert summary["products_created"] == 1
            assert summary["store_links_created"] == 1
            assert summary["price_points_created"] == 1

            restored = (
                await db_session.execute(select(Product).where(Product.name == product_name))
            ).scalar_one()
            assert restored.brand == "Lambi"
            assert restored.unit == "st"

            restored_link = (
                await db_session.execute(
                    select(ProductStore).where(ProductStore.store_url == store_url)
                )
            ).scalar_one()
            assert restored_link.store_label == "ICA Maxi Sandviken"
            assert restored_link.package_quantity == Decimal("24.00")
            assert restored_link.scraped_package_quantity == Decimal("24.00")
            # NULL schedule = inherit the store's; a restore must not invent an override.
            assert restored_link.check_frequency_hours is None
            assert restored_link.check_weekdays is None

            point = (
                await db_session.execute(
                    select(PricePoint).where(PricePoint.product_store_id == restored_link.id)
                )
            ).scalar_one()
            assert point.price_sek == Decimal("108.95")
            assert point.offer_price_sek == Decimal("89.90")
            assert point.offer_type == "kampanj"
            assert point.checked_at == checked_at
        finally:
            found = (
                await db_session.execute(select(Product).where(Product.name == product_name))
            ).scalar_one_or_none()
            if found is not None:
                await _wipe(db_session, found.id)

    async def test_reimport_is_idempotent(self, client, db_session) -> None:
        """Importing the same backup twice must not duplicate the price history."""
        product, link, _ = await _seed(db_session)
        product_name, store_url = product.name, link.store_url
        try:
            async with client as c:
                r = await c.get("/export", params={"include_history": True})
                payload = _only_mine(r.json(), product_name, store_url)
                files = {"file": ("backup.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)

            assert r.status_code == 200, r.text
            # Nothing was wiped, so every row already exists.
            summary = r.json()["summary"]
            assert summary["store_links_created"] == 0
            assert summary["price_points_created"] == 0
            assert summary["price_points_skipped"] == 1

            count = len(
                (
                    await db_session.execute(
                        select(PricePoint)
                        .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                        .where(ProductStore.store_url == store_url)
                    )
                )
                .scalars()
                .all()
            )
            assert count == 1
        finally:
            await _wipe(db_session, product.id)


class TestVersionCompatibility:
    async def test_v1_0_file_still_imports(self, client, db_session) -> None:
        """An old 1.0 backup must not become unreadable: it simply carries no store_label and
        no store_url on history rows, so those parts degrade instead of failing."""
        store = (await db_session.execute(select(Store).where(Store.slug == "ica"))).scalar_one()
        url = f"https://handlaprivatkund.ica.se/stores/1004247/products/{uuid.uuid4()}"
        name = f"{_MARKER} Gammal Export"
        payload = {
            "version": "1.0",
            "products": [
                {
                    "name": name,
                    "brand": "Lambi",
                    "category": "Skafferi",
                    "unit": "st",
                    "store_links": [
                        {
                            "store_slug": store.slug,
                            "store_url": url,
                            "is_active": True,
                            "package_size": "24-pack",
                            "package_quantity": 24.0,
                        }
                    ],
                    "watches": [],
                }
            ],
            # 1.0 history has no store_url — unaddressable, so it is skipped, not guessed at.
            "price_history": [
                {"product_id": str(uuid.uuid4()), "store_slug": store.slug, "price_sek": 10.0}
            ],
        }
        product_id = None
        try:
            async with client as c:
                files = {"file": ("old.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text
            summary = r.json()["summary"]
            assert summary["products_created"] == 1
            assert summary["price_points_created"] == 0
            assert summary["price_points_skipped"] == 1

            restored = (
                await db_session.execute(select(Product).where(Product.name == name))
            ).scalar_one()
            product_id = restored.id
        finally:
            if product_id is not None:
                await _wipe(db_session, product_id)

    async def test_unknown_version_is_rejected(self, client) -> None:
        payload = {"version": "9.9", "products": []}
        async with client as c:
            files = {"file": ("x.json", json.dumps(payload).encode(), "application/json")}
            r = await c.post("/import", files=files)
        assert r.status_code == 400


class TestImportInvariants:
    """The import is the one write path fed by a FILE — it must re-apply the invariants
    every other write path enforces, because a pre-v0.32.1 backup (or a hand-edited one)
    would otherwise restore exactly the corruptions those paths refuse."""

    def _payload(self, store_slug: str, url: str, name: str, **overrides) -> dict:
        base = {
            "version": "1.1",
            "products": [
                {
                    "name": name,
                    "brand": None,
                    "category": None,
                    "unit": "st",
                    "store_links": [
                        {
                            "store_slug": store_slug,
                            "store_url": url,
                            "is_active": True,
                            "package_size": "24-pack",
                            "package_quantity": 24.0,
                        }
                    ],
                    "watches": [],
                }
            ],
            "price_history": [],
        }
        base["products"][0].update(overrides.pop("product", {}))
        base.update(overrides)
        return base

    async def test_an_inverted_offer_is_imported_without_the_offer(
        self, client, db_session
    ) -> None:
        """offer >= price is refused wholesale — price, type AND details — exactly as
        the extractors and the manual endpoint refuse it (v0.32.1). Restored as-is it
        would re-price the link at the HIGHER number in every ranking."""
        store = (await db_session.execute(select(Store).where(Store.slug == "ica"))).scalar_one()
        url = f"https://handlaprivatkund.ica.se/stores/1004247/products/{uuid.uuid4()}"
        name = f"{_MARKER} Inverterad"
        payload = self._payload(store.slug, url, name)
        payload["price_history"] = [
            {
                "store_url": url,
                "checked_at": _now().isoformat(),
                "price_sek": 69.46,
                "offer_price_sek": 130.00,
                "offer_type": "kampanj",
                "offer_details": "2 för 130 kr",
            }
        ]
        product_id = None
        try:
            async with client as c:
                files = {"file": ("x.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text
            summary = r.json()["summary"]
            assert summary["price_points_created"] == 1
            assert any("erbjudande" in w.lower() for w in summary["warnings"])

            link = (
                await db_session.execute(select(ProductStore).where(ProductStore.store_url == url))
            ).scalar_one()
            product_id = link.product_id
            point = (
                await db_session.execute(
                    select(PricePoint).where(PricePoint.product_store_id == link.id)
                )
            ).scalar_one()
            assert point.price_sek == Decimal("69.46")
            assert point.offer_price_sek is None
            assert point.offer_type is None
            assert point.offer_details is None
        finally:
            if product_id is not None:
                await _wipe(db_session, product_id)

    async def test_a_negative_amount_is_imported_without_the_amount(
        self, client, db_session
    ) -> None:
        """A negative amount gives a negative kr/unit, which sorts FIRST in every
        cheapest-per-unit ranking — dropped with a warning, landing the link in the
        'saknar mängd' facet instead."""
        store = (await db_session.execute(select(Store).where(Store.slug == "ica"))).scalar_one()
        url = f"https://handlaprivatkund.ica.se/stores/1004247/products/{uuid.uuid4()}"
        name = f"{_MARKER} Negativ mängd"
        payload = self._payload(store.slug, url, name)
        payload["products"][0]["store_links"][0]["package_quantity"] = -24.0
        product_id = None
        try:
            async with client as c:
                files = {"file": ("x.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text
            assert any("mängd" in w for w in r.json()["summary"]["warnings"])

            link = (
                await db_session.execute(select(ProductStore).where(ProductStore.store_url == url))
            ).scalar_one()
            product_id = link.product_id
            assert link.package_quantity is None
        finally:
            if product_id is not None:
                await _wipe(db_session, product_id)

    async def test_an_existing_products_unit_is_never_overwritten(self, client, db_session) -> None:
        """The same one-way door PUT /products refuses: unit scales every link's
        quantity, so an import that flips it silently misscales the product's whole
        kr/unit history."""
        product, link, _ = await _seed(db_session)
        payload = self._payload(
            "ica", link.store_url, product.name, product={"unit": "kg", "brand": product.brand}
        )
        try:
            async with client as c:
                files = {"file": ("x.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text
            assert any("enheten" in w.lower() for w in r.json()["summary"]["warnings"])

            await db_session.refresh(product)
            assert product.unit == "st"
        finally:
            await _wipe(db_session, product.id)

    async def test_an_unknown_unit_is_imported_without_a_unit(self, client, db_session) -> None:
        """ "ml" is not a canonical unit, and unit is immutable once set — imported as
        None (repairable) rather than as a value that flags a conflict on every scrape
        and can only be fixed by delete-and-recreate."""
        store = (await db_session.execute(select(Store).where(Store.slug == "ica"))).scalar_one()
        url = f"https://handlaprivatkund.ica.se/stores/1004247/products/{uuid.uuid4()}"
        name = f"{_MARKER} Ml-enhet"
        payload = self._payload(store.slug, url, name, product={"unit": "ml"})
        product_id = None
        try:
            async with client as c:
                files = {"file": ("x.json", json.dumps(payload).encode(), "application/json")}
                r = await c.post("/import", files=files)
            assert r.status_code == 200, r.text

            restored = (
                await db_session.execute(select(Product).where(Product.name == name))
            ).scalar_one()
            product_id = restored.id
            assert restored.unit is None
        finally:
            if product_id is not None:
                await _wipe(db_session, product_id)

    async def test_an_oversized_file_is_refused(self, client) -> None:
        """file.read() buffers the whole upload in memory — a cap, not an OOM."""
        blob = b'{"version": "1.1", "padding": "' + b"x" * (21 * 1024 * 1024) + b'"}'
        async with client as c:
            files = {"file": ("big.json", blob, "application/json")}
            r = await c.post("/import", files=files)
        assert r.status_code == 413
