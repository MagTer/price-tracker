"""Tests for quick-add: pure domain logic + the two API endpoints.

The pure tier (store matching, package parsing, unit derivation, product suggestion,
JSON-LD metadata) runs without a network, a DB, or an LLM — that is the point of
domain/quickadd.py being pure. The endpoint tier follows test_api.py's pattern: TestClient,
dependency overrides, real transient ORM objects, and a patched fetcher/parser so nothing
leaves the process.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import require_auth
from domain.extractors.jsonld import JsonLdExtractor
from domain.models import Product, Store
from domain.quickadd import (
    derive_unit,
    match_store_by_url,
    parse_package_from_name,
    suggest_existing_products,
)

# --- Pure tier -------------------------------------------------------------------------


def _stores():
    return [
        SimpleNamespace(name="ICA", base_url="https://handlaprivatkund.ica.se"),
        SimpleNamespace(name="Willys", base_url="https://www.willys.se"),
        SimpleNamespace(name="Apotea", base_url="https://www.apotea.se"),
    ]


class TestMatchStoreByUrl:
    def test_matches_www_stripped_hostname(self):
        store = match_store_by_url("https://willys.se/produkt/x-123_ST", _stores())
        assert store is not None and store.name == "Willys"

    def test_matches_subdomain_exactly(self):
        store = match_store_by_url("https://handlaprivatkund.ica.se/butiker/x/produkt/y", _stores())
        assert store is not None and store.name == "ICA"

    def test_parent_domain_does_not_match_shop_subdomain(self):
        """www.ica.se (recipes) is NOT handlaprivatkund.ica.se (the shop) — no suffix match."""
        assert match_store_by_url("https://www.ica.se/recept/kladdkaka", _stores()) is None

    def test_unknown_host_is_none(self):
        assert match_store_by_url("https://www.coop.se/produkt/x", _stores()) is None

    def test_garbage_url_is_none(self):
        assert match_store_by_url("not a url", _stores()) is None


class TestParsePackageFromName:
    def test_pack_count(self):
        g = parse_package_from_name("Lambi Toalettpapper 24-pack")
        assert (g.amount, g.entry_unit, g.pack_size, g.label) == (
            Decimal("24"),
            "st",
            24,
            "24-pack",
        )

    def test_amount_with_unit_wins_over_pack(self):
        g = parse_package_from_name("Coca-Cola 1,5 l 4-pack")
        assert g.amount == Decimal("1.5") and g.entry_unit == "l"
        assert g.pack_size == 4

    def test_ml(self):
        g = parse_package_from_name("Schampo 500 ml")
        assert g.amount == Decimal("500") and g.entry_unit == "ml"
        assert g.label == "500 ml"

    def test_grams(self):
        g = parse_package_from_name("Tvättmedel 750g")
        assert g.amount == Decimal("750") and g.entry_unit == "g"

    def test_lager_is_not_liters(self):
        """'3 lager' (ply) must not read as '3 l'."""
        g = parse_package_from_name("Toalettpapper 3 lager")
        assert g.entry_unit is None and g.amount is None

    def test_rullar_counts_as_pack(self):
        g = parse_package_from_name("Hushållspapper 8 rullar")
        assert g.pack_size == 8 and g.entry_unit == "st"

    def test_no_signal(self):
        g = parse_package_from_name("Alvedon")
        assert (g.amount, g.entry_unit, g.pack_size, g.label) == (None, None, None, None)

    def test_none_name(self):
        g = parse_package_from_name(None)
        assert g.amount is None


class TestDeriveUnit:
    @pytest.mark.parametrize(
        ("entry", "pack", "expected"),
        [
            ("ml", None, "liter"),
            ("l", None, "liter"),
            ("g", None, "kg"),
            ("kg", None, "kg"),
            ("st", None, "st"),
            (None, 24, "st"),
            (None, None, None),
            ("furlong", None, None),
        ],
    )
    def test_mapping(self, entry, pack, expected):
        assert derive_unit(entry, pack) == expected


class TestSuggestExistingProducts:
    CANDIDATES = [
        {"id": "1", "name": "Lambi toalettpapper", "brand": "Lambi", "unit": "st"},
        {"id": "2", "name": "Serla hushållspapper", "brand": "Serla", "unit": "st"},
        {"id": "3", "name": "Alvedon 500 mg", "brand": None, "unit": "st"},
    ]

    def test_overlapping_tokens_match(self):
        got = suggest_existing_products("Lambi Toalettpapper 3-lager 24-pack", self.CANDIDATES)
        assert [s["id"] for s in got] == ["1"]
        assert got[0]["match_score"] == 2  # lambi + toalettpapper

    def test_brand_counts_toward_the_match(self):
        got = suggest_existing_products("Serla Rulle", self.CANDIDATES)
        assert [s["id"] for s in got] == ["2"]

    def test_no_match_is_empty(self):
        assert suggest_existing_products("Coca-Cola Zero", self.CANDIDATES) == []

    def test_short_tokens_do_not_fake_overlap(self):
        """Tokens under 3 chars ('3', 'mg') never score."""
        assert suggest_existing_products("X 3", self.CANDIDATES) == []

    def test_none_name_is_empty(self):
        assert suggest_existing_products(None, self.CANDIDATES) == []


_JSONLD_HTML = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"Lambi Toalettpapper 3-lager 24-pack",
 "brand":{"@type":"Brand","name":"Lambi"},
 "offers":{"@type":"Offer","price":"89,90","priceCurrency":"SEK",
           "availability":"https://schema.org/InStock"}}
</script></head><body>x</body></html>"""


class TestButikConfigLoaders:
    """Env-driven butik config (QUICKADD_STORE_LABELS / QUICKADD_SIBLING_GROUPS): another
    instance shops at other stores, so the ids are operator data. Malformed JSON must fall
    back to the built-in defaults WITH a warning — a typo must not silently turn quick-add's
    suggestions off."""

    def test_labels_from_valid_json(self):
        from domain.quickadd import load_store_labels

        got = load_store_labels('{"1234": "ICA Nära Hemma", "5678": "ICA Kvantum Borta"}')
        assert got == {"1234": "ICA Nära Hemma", "5678": "ICA Kvantum Borta"}

    def test_labels_empty_env_gives_defaults(self):
        from domain.quickadd import _DEFAULT_STORE_LABELS, load_store_labels

        assert load_store_labels(None) == _DEFAULT_STORE_LABELS
        assert load_store_labels("  ") == _DEFAULT_STORE_LABELS

    def test_labels_malformed_json_falls_back_to_defaults(self):
        from domain.quickadd import _DEFAULT_STORE_LABELS, load_store_labels

        assert load_store_labels("{not json") == _DEFAULT_STORE_LABELS
        assert load_store_labels('["a", "list"]') == _DEFAULT_STORE_LABELS

    def test_groups_from_valid_json(self):
        from domain.quickadd import load_sibling_groups

        got = load_sibling_groups('[["1234", "5678"], ["9", "10", "11"]]')
        assert got == (frozenset({"1234", "5678"}), frozenset({"9", "10", "11"}))

    def test_groups_empty_env_gives_defaults(self):
        from domain.quickadd import _DEFAULT_SIBLING_GROUPS, load_sibling_groups

        assert load_sibling_groups(None) == _DEFAULT_SIBLING_GROUPS

    def test_groups_malformed_json_falls_back_to_defaults(self):
        from domain.quickadd import _DEFAULT_SIBLING_GROUPS, load_sibling_groups

        assert load_sibling_groups('{"not": "a list"}') == _DEFAULT_SIBLING_GROUPS
        assert load_sibling_groups('["flat", "not-nested"]') == _DEFAULT_SIBLING_GROUPS

    def test_empty_inner_groups_are_dropped(self):
        from domain.quickadd import load_sibling_groups

        assert load_sibling_groups('[[], ["1", "2"]]') == (frozenset({"1", "2"}),)


class TestExtractProductMetadata:
    """The LLM metadata fallback — cascade order and the acceptance floor."""

    def _parser(self):
        from domain.parser import PriceParser

        return PriceParser()

    async def test_returns_first_confident_parse_normalized(self):
        parser = self._parser()
        payload = {
            "name": "Falu Rågrut",
            "brand": "Wasa",
            "category": "knäckebröd",
            "price": 29.9,
            "pack_size": None,
            "package_amount": 500,
            "package_unit": "G",
            "confidence": 0.9,
        }
        with patch.object(parser, "_call_model_json", AsyncMock(return_value=payload)):
            meta = await parser.extract_product_metadata("page text", "willys")
        assert meta is not None
        assert meta.name == "Falu Rågrut"
        assert meta.package_unit == "g"  # lowercased like the price prompt's parse
        assert meta.price_sek == Decimal("29.9")
        assert meta.source == "llm"

    async def test_below_floor_on_all_models_is_none(self):
        parser = self._parser()
        low = {"name": "X", "confidence": 0.2}
        with patch.object(parser, "_call_model_json", AsyncMock(return_value=low)):
            assert await parser.extract_product_metadata("page text", "ica") is None

    async def test_model_errors_fall_through_to_none(self):
        parser = self._parser()
        boom = AsyncMock(side_effect=RuntimeError("api down"))
        with patch.object(parser, "_call_model_json", boom):
            assert await parser.extract_product_metadata("page text", "ica") is None
        # Every cascade model was tried before giving up.
        assert boom.await_count == len(parser.MODEL_CASCADE)


class TestJsonLdMetadata:
    def test_name_and_brand_node(self):
        meta = JsonLdExtractor().extract_product_metadata(_JSONLD_HTML)
        assert meta == {"name": "Lambi Toalettpapper 3-lager 24-pack", "brand": "Lambi"}

    def test_brand_as_plain_string(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Alvedon","brand":"GSK"}</script>'
        )
        meta = JsonLdExtractor().extract_product_metadata(html)
        assert meta == {"name": "Alvedon", "brand": "GSK"}

    def test_no_product_node_is_none(self):
        assert JsonLdExtractor().extract_product_metadata("<html></html>") is None


# --- Endpoint tier ---------------------------------------------------------------------

TENANT_STORE_ID = uuid.uuid4()


def _store(slug="apotea", base_url="https://www.apotea.se", check_weekdays=None):
    # Schedule fields set explicitly: ORM defaults apply at flush, and these Store
    # objects never touch a session — unset they would be None, not the DB's 72.
    return Store(
        id=TENANT_STORE_ID,
        name=slug.capitalize(),
        slug=slug,
        store_type="pharmacy",
        base_url=base_url,
        check_weekdays=check_weekdays,
        check_frequency_hours=72,
        is_active=True,
    )


def _product(name="Lambi toalettpapper", brand="Lambi", unit="st"):
    return Product(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=name,
        brand=brand,
        category=None,
        unit=unit,
    )


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def client(mock_session):
    app = create_app()

    async def override_auth():
        return "test@example.com"

    async def override_get_db():
        yield mock_session

    from api.admin import get_db as admin_get_db

    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[admin_get_db] = override_get_db
    return TestClient(app)


@pytest.fixture
def mock_service(client):
    from api.admin import get_price_tracker_service as admin_get_service

    service = MagicMock()
    product = MagicMock()
    product.id = uuid.uuid4()
    service.create_product = AsyncMock(return_value=product)
    link = MagicMock()
    link.id = uuid.uuid4()
    service.link_product_store = AsyncMock(return_value=link)
    service.delete_product = AsyncMock(return_value=None)
    client.app.dependency_overrides[admin_get_service] = lambda: service
    return service


def _result(scalars_all=None, first=None, scalar_one_or_none=None):
    """A MagicMock shaped like a SQLAlchemy Result for the three access styles used."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = scalars_all or []
    r.first.return_value = first
    r.scalar_one_or_none.return_value = scalar_one_or_none
    return r


class TestQuickAddPreview:
    def test_rejects_non_http_url(self, client):
        r = client.post("/quick-add/preview", json={"url": "ftp://x"})
        assert r.status_code == 400

    def test_unmatched_host_is_422_and_names_the_stores(self, client, mock_session):
        mock_session.execute.return_value = _result(scalars_all=[_store()])
        r = client.post("/quick-add/preview", json={"url": "https://www.coop.se/p/1"})
        assert r.status_code == 422
        assert "Apotea" in r.json()["detail"]

    def test_already_tracked_short_circuits_before_fetch(self, client, mock_session):
        product = _product()
        mock_session.execute.side_effect = [
            _result(scalars_all=[_store()]),
            _result(first=(product.id, product.name)),
        ]
        fetcher = MagicMock()
        with patch("api.admin.get_fetcher", return_value=fetcher):
            r = client.post("/quick-add/preview", json={"url": "https://www.apotea.se/p/1"})
        assert r.status_code == 200
        assert r.json()["already_tracked"]["product_name"] == product.name
        fetcher.fetch.assert_not_called()

    def test_jsonld_page_fills_the_preview_without_llm(self, client, mock_session):
        existing = _product()
        mock_session.execute.side_effect = [
            _result(scalars_all=[_store()]),
            _result(first=None),
            _result(scalars_all=[existing]),
        ]
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={"ok": True, "text": "page", "html": _JSONLD_HTML, "error": None}
        )
        parser = MagicMock()
        parser.extract_product_metadata = AsyncMock()
        with (
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.PriceParser", return_value=parser),
        ):
            r = client.post("/quick-add/preview", json={"url": "https://www.apotea.se/p/1"})

        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Lambi Toalettpapper 3-lager 24-pack"
        assert body["brand"] == "Lambi"
        assert body["price_sek"] == pytest.approx(89.90)
        assert body["source"] == "jsonld"
        # Package parsed from the TITLE with coded logic: 24-pack → st, 24.
        assert body["suggested_unit"] == "st"
        assert body["package_quantity"] == pytest.approx(24.0)
        assert body["package_size"] == "24-pack"
        # The existing Lambi product surfaced as a possible "new link" target.
        assert [s["id"] for s in body["existing_products"]] == [str(existing.id)]
        # JSON-LD sufficed — the LLM fallback must not have been consulted.
        parser.extract_product_metadata.assert_not_called()
        # Apotea has no offer cycle — the store schedule (which the link INHERITS)
        # is interval mode; the preview reports it for the confirm step's info line.
        assert body["store_schedule"] == {"weekdays": [], "frequency_hours": 72}

    def test_fetch_failure_is_502(self, client, mock_session):
        mock_session.execute.side_effect = [
            _result(scalars_all=[_store()]),
            _result(first=None),
        ]
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={"ok": False, "text": "", "html": "", "error": "boom"}
        )
        with patch("api.admin.get_fetcher", return_value=fetcher):
            r = client.post("/quick-add/preview", json={"url": "https://www.apotea.se/p/1"})
        assert r.status_code == 502

    def test_llm_fallback_when_no_jsonld(self, client, mock_session):
        from domain.result import ProductMetadata

        mock_session.execute.side_effect = [
            _result(
                scalars_all=[
                    _store(
                        slug="willys",
                        base_url="https://www.willys.se",
                        check_weekdays=[0, 4],
                    )
                ]
            ),
            _result(first=None),
            _result(scalars_all=[]),
        ]
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={"ok": True, "text": "page text", "html": "<html></html>", "error": None}
        )
        parser = MagicMock()
        parser.extract_product_metadata = AsyncMock(
            return_value=ProductMetadata(
                name="Falu Rågrut",
                brand="Wasa",
                category="knäckebröd",
                price_sek=Decimal("29.90"),
                package_amount=Decimal("500"),
                package_unit="g",
                pack_size=None,
                confidence=0.9,
                source="llm",
            )
        )
        with (
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.PriceParser", return_value=parser),
        ):
            r = client.post("/quick-add/preview", json={"url": "https://willys.se/produkt/x"})

        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Falu Rågrut"
        assert body["source"] == "llm"
        assert body["category"] == "knäckebröd"
        assert body["suggested_unit"] == "kg"
        assert body["package_quantity"] == pytest.approx(0.5)  # 500 g normalized to kg
        assert body["price_sek"] == pytest.approx(29.90)
        # Willys publishes offers Mondays AND Fridays — the STORE schedule carries the
        # days; the link inherits them, so the preview only reports, never prefills.
        assert body["store_schedule"] == {"weekdays": [0, 4], "frequency_hours": 72}


class TestQuickAddConfirm:
    URL = "https://www.apotea.se/p/1"

    def _post(self, client, **overrides):
        body = {
            "url": self.URL,
            "store_id": str(TENANT_STORE_ID),
            "name": "Lambi toalettpapper",
            "brand": "Lambi",
            "unit": "st",
            "package_size": "24-pack",
            "package_quantity": 24,
            "run_first_check": False,
        }
        body.update(overrides)
        return client.post("/quick-add", json=body)

    def test_creates_product_and_link(self, client, mock_session, mock_service):
        mock_session.execute.return_value = _result(first=None)
        r = self._post(client)
        assert r.status_code == 201
        body = r.json()
        assert body["created_product"] is True
        assert body["check"] is None  # run_first_check=False
        mock_service.create_product.assert_awaited_once()
        kwargs = mock_service.link_product_store.await_args.kwargs
        assert kwargs["store_url"] == self.URL
        assert kwargs["package_quantity"] == Decimal("24")

    def test_link_inherits_the_store_schedule(self, client, mock_session, mock_service):
        """Quick-add sets NO per-link schedule — the link follows its store. The override
        lives in the link-edit dialog, not in the add flow."""
        mock_session.execute.return_value = _result(first=None)
        r = self._post(client)
        assert r.status_code == 201
        kwargs = mock_service.link_product_store.await_args.kwargs
        assert "check_frequency_hours" not in kwargs
        assert "check_weekdays" not in kwargs

    def test_duplicate_url_is_409_and_creates_nothing(self, client, mock_session, mock_service):
        mock_session.execute.return_value = _result(first=(uuid.uuid4(),))
        r = self._post(client)
        assert r.status_code == 409
        mock_service.create_product.assert_not_awaited()
        mock_service.link_product_store.assert_not_awaited()

    def test_existing_product_links_without_creating(self, client, mock_session, mock_service):
        existing = _product()
        mock_session.execute.side_effect = [
            _result(first=None),
            _result(scalar_one_or_none=existing),
        ]
        r = self._post(client, product_id=str(existing.id), name=None)
        assert r.status_code == 201
        assert r.json()["created_product"] is False
        mock_service.create_product.assert_not_awaited()
        assert mock_service.link_product_store.await_args.kwargs["product_id"] == str(existing.id)

    def test_unknown_existing_product_is_404(self, client, mock_session, mock_service):
        mock_session.execute.side_effect = [
            _result(first=None),
            _result(scalar_one_or_none=None),
        ]
        r = self._post(client, product_id=str(uuid.uuid4()), name=None)
        assert r.status_code == 404
        mock_service.link_product_store.assert_not_awaited()

    def test_new_product_requires_a_name(self, client, mock_service):
        r = self._post(client, name="  ")
        assert r.status_code == 400

    def test_bad_unit_is_rejected(self, client, mock_service):
        r = self._post(client, unit="gram")
        assert r.status_code == 400

    def test_link_integrity_error_rolls_back_the_created_product(
        self, client, mock_session, mock_service
    ):
        from sqlalchemy.exc import IntegrityError

        mock_session.execute.return_value = _result(first=None)
        mock_service.link_product_store = AsyncMock(
            side_effect=IntegrityError("dup", None, Exception("dup"))
        )
        r = self._post(client)
        assert r.status_code == 409
        # The just-created product must not be left orphaned.
        mock_service.delete_product.assert_awaited_once()

    def test_first_check_runs_and_reports_the_price(self, client, mock_session, mock_service):
        """run_first_check=True routes through perform_price_check and returns the price."""
        from domain.models import ProductStore
        from domain.result import PriceExtractionResult

        product, store = _product(unit="st"), _store()
        link = ProductStore(
            id=uuid.uuid4(),
            product_id=product.id,
            store_id=store.id,
            store_url=self.URL,
            check_frequency_hours=72,
            package_size="24-pack",
            package_quantity=Decimal("24"),
        )
        row_result = MagicMock()
        row_result.one_or_none.return_value = (link, store, product)
        mock_session.execute.side_effect = [_result(first=None), row_result]

        extraction = PriceExtractionResult(
            price_sek=Decimal("89.90"),
            store_unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.95,
            pack_size=None,
            package_amount=None,
            package_unit=None,
            raw_response={"source": "llm"},
        )
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value={"ok": True, "text": "page", "html": ""})
        parser = MagicMock()
        parser.extract_price = AsyncMock(return_value=extraction)

        with (
            patch("api.admin.get_fetcher", return_value=fetcher),
            patch("api.admin.PriceParser", return_value=parser),
        ):
            r = self._post(client, run_first_check=True)

        assert r.status_code == 201
        check = r.json()["check"]
        assert check["success"] is True
        assert check["price_sek"] == pytest.approx(89.90)
        assert check["unit_price_sek"] == pytest.approx(3.75)  # 89.90 / 24, rounded

    def test_first_check_failure_still_returns_201(self, client, mock_session, mock_service):
        """Creation is the task; a dead page leaves the first price to the scheduler."""
        from domain.models import ProductStore

        product, store = _product(unit="st"), _store()
        link = ProductStore(
            id=uuid.uuid4(),
            product_id=product.id,
            store_id=store.id,
            store_url=self.URL,
            check_frequency_hours=72,
        )
        row_result = MagicMock()
        row_result.one_or_none.return_value = (link, store, product)
        mock_session.execute.side_effect = [_result(first=None), row_result]

        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(
            return_value={"ok": False, "text": "", "html": "", "error": "timeout"}
        )
        with patch("api.admin.get_fetcher", return_value=fetcher):
            r = self._post(client, run_first_check=True)

        assert r.status_code == 201
        check = r.json()["check"]
        assert check == {"success": False, "reason": "fetch_failed"}
