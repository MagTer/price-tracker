"""Tests for domain/blad.py — THE butiksblad offer analysis.

The fixture mirrors the live shape spiked 2026-08-03 (Willys Sandviken 2211, offer
2500310468 "Läsk 15-pack"): the campaign price is a NUMBER in potentialPromotions[].price,
ordinarie lives in priceNoUnit, the printed jämförpris uses a COLON decimal separator
("12:08 kr/l +pant"), and the package is a multipack volume ("15p/33cl").
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.blad import (
    BladOffer,
    match_candidates,
    offer_unit_price,
    parse_offer_code,
    parse_offline_offer,
)
from domain.models import PricePoint, Product, ProductStore, Store
from domain.tenant import DEFAULT_TENANT_ID

URL = "https://www.willys.se/erbjudanden/offline-Lask-15-pack-2500310468"


def _payload(**overrides) -> dict:
    payload = {
        "priceNoUnit": "99,90",
        "priceUnit": "kr/st",
        "displayVolume": "15p/33cl",
        "online": False,
        "manufacturer": "COCA-COLA • COCA-COLA ZERO",
        "potentialPromotions": [
            {
                "price": 59.8,
                "comparePrice": "12:08 kr/l +pant",
                "code": "2500310468",
                "weightVolume": "15p/33cl",
                "campaignType": "LOYALTY",
                "redeemLimitLabel": "Max 3 köp",
                "qualifyingCount": 1,
                "cartLabel": "59,80/st  +pant",
                "validUntil": 1786312799000,
                "rewardLabel": "59,80/st  +pant",
                "brands": ["COCA-COLA • COCA-COLA ZERO"],
                "name": "Läsk 15-pack",
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestParseOfferCode:
    def test_blad_url(self) -> None:
        assert parse_offer_code(URL) == "2500310468"

    def test_bare_code(self) -> None:
        assert parse_offer_code("2500310468") == "2500310468"

    def test_no_code(self) -> None:
        assert parse_offer_code("https://www.willys.se/erbjudanden") is None


class TestParseOfflineOffer:
    def test_live_shape(self) -> None:
        offer = parse_offline_offer("2500310468", _payload())
        assert offer is not None
        assert offer.name == "Läsk 15-pack"
        assert offer.offer_price_sek == Decimal("59.80")
        assert offer.ordinarie_price_sek == Decimal("99.90")
        # "12:08 kr/l +pant" — colon decimals, unit, deposit marker.
        assert offer.unit_price_sek == Decimal("12.08")
        assert offer.unit_price_unit == "liter"
        assert offer.excludes_deposit is True
        # "15p/33cl" → 15 × 0.33 l.
        assert offer.package_amount == Decimal("4.95")
        assert offer.package_unit == "liter"
        assert "Max 3 köp" in offer.conditions
        assert "Kräver Willys Plus" in offer.conditions
        assert offer.valid_until == "2026-08-09"

    def test_offer_at_or_above_ordinarie_is_refused(self) -> None:
        payload = _payload(priceNoUnit="49,90")  # promo 59.8 >= ordinarie
        assert parse_offline_offer("x", payload) is None

    def test_no_numeric_promotion_price_is_refused(self) -> None:
        payload = _payload()
        payload["potentialPromotions"][0]["price"] = None
        assert parse_offline_offer("x", payload) is None

    def test_no_promotions_is_none(self) -> None:
        assert parse_offline_offer("x", _payload(potentialPromotions=[])) is None

    def test_several_promotions_pick_the_highest(self) -> None:
        payload = _payload()
        cheaper = dict(payload["potentialPromotions"][0], price=39.9, name="Extrapris")
        payload["potentialPromotions"].append(cheaper)
        offer = parse_offline_offer("x", payload)
        assert offer is not None
        assert offer.offer_price_sek == Decimal("59.80")

    def test_multibuy_condition_is_visible(self) -> None:
        payload = _payload()
        payload["potentialPromotions"][0]["qualifyingCount"] = 2
        offer = parse_offline_offer("x", payload)
        assert offer is not None
        assert "Kräver 2 st" in offer.conditions


class TestOfferUnitPrice:
    def test_printed_figure_wins(self) -> None:
        offer = parse_offline_offer("x", _payload())
        assert offer is not None
        assert offer_unit_price(offer) == (Decimal("12.08"), "liter")

    def test_computed_fallback_from_the_package(self) -> None:
        payload = _payload()
        payload["potentialPromotions"][0]["comparePrice"] = None
        offer = parse_offline_offer("x", payload)
        assert offer is not None
        price, unit = offer_unit_price(offer)
        assert price == Decimal("12.08")  # 59.80 / 4.95, the store's own arithmetic
        assert unit == "liter"

    def test_no_figure_and_no_package_is_none(self) -> None:
        offer = BladOffer(
            code="x",
            name="Okänt",
            brands=[],
            ordinarie_price_sek=None,
            offer_price_sek=Decimal("10"),
            package_text=None,
            package_amount=None,
            package_unit=None,
            unit_price_sek=None,
            unit_price_unit=None,
            excludes_deposit=False,
        )
        assert offer_unit_price(offer) == (None, None)


@pytest.mark.integration
class TestMatchCandidates:
    @pytest.mark.asyncio
    async def test_matches_by_token_overlap_with_comparable_diff(self, db_session) -> None:
        store = (await db_session.execute(select(Store).where(Store.slug == "willys"))).scalar_one()
        laesk = Product(
            tenant_id=DEFAULT_TENANT_ID,
            name="Läsk Cola Zero 33cl 20-p",
            brand="Coca-Cola",
            category=None,
            unit="liter",
        )
        soap = Product(
            tenant_id=DEFAULT_TENANT_ID,
            name="Handtvål",
            brand="Palmolive",
            category=None,
            unit="liter",
        )
        db_session.add_all([laesk, soap])
        await db_session.flush()
        link = ProductStore(
            product_id=laesk.id,
            store_id=store.id,
            store_url=f"https://www.willys.se/{uuid.uuid4()}",
            package_quantity=Decimal("6.6"),
            is_active=True,
        )
        db_session.add(link)
        await db_session.flush()
        db_session.add(
            PricePoint(
                product_store_id=link.id,
                price_sek=Decimal("137.45"),
                checked_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db_session.flush()

        offer = parse_offline_offer("2500310468", _payload())
        assert offer is not None
        candidates = await match_candidates(db_session, offer)

        assert len(candidates) == 1  # the soap shares no token and must not appear
        candidate = candidates[0]
        assert candidate["product_name"] == "Läsk Cola Zero 33cl 20-p"
        assert candidate["current_unit_price_sek"] == pytest.approx(20.83)
        # Offer 12.08 kr/l against tracked 20.83 kr/l — the blad is cheaper.
        assert candidate["unit_price_diff_sek"] == pytest.approx(-8.75)

    @pytest.mark.asyncio
    async def test_unit_mismatch_yields_no_diff(self, db_session) -> None:
        store = (await db_session.execute(select(Store).where(Store.slug == "willys"))).scalar_one()
        product = Product(
            tenant_id=DEFAULT_TENANT_ID,
            name="Läsk Cola Zero burkar",
            brand="Coca-Cola",
            category=None,
            unit="st",  # counted per can, while the offer prints kr/l
        )
        db_session.add(product)
        await db_session.flush()
        link = ProductStore(
            product_id=product.id,
            store_id=store.id,
            store_url=f"https://www.willys.se/{uuid.uuid4()}",
            package_quantity=Decimal("20"),
            is_active=True,
        )
        db_session.add(link)
        await db_session.flush()
        db_session.add(
            PricePoint(
                product_store_id=link.id,
                price_sek=Decimal("137.45"),
                checked_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db_session.flush()

        offer = parse_offline_offer("2500310468", _payload())
        assert offer is not None
        candidates = await match_candidates(db_session, offer)

        assert len(candidates) == 1
        assert candidates[0]["unit_price_diff_sek"] is None  # kr/l against kr/st is nonsense


@pytest.mark.integration
class TestNotableLinks:
    """Where a manual note may honestly land: the product's links AT THE OFFER'S OWN
    STORE. The price was observed at Willys, so writing it onto an ICA link would forge
    the observation — and a manual point is indistinguishable from a fetched one once
    written (no path deletes a single point)."""

    async def _product_with_links(self, session, name: str, slugs: list[str]) -> Product:
        product = Product(
            tenant_id=DEFAULT_TENANT_ID,
            name=name,
            brand="Coca-Cola",
            category=None,
            unit="liter",
        )
        session.add(product)
        await session.flush()
        for slug in slugs:
            store = (
                await session.execute(select(Store).where(Store.slug == slug))
            ).scalar_one()
            link = ProductStore(
                product_id=product.id,
                store_id=store.id,
                store_url=f"https://example.test/{uuid.uuid4()}",
                package_size=f"{slug}-pack",
                package_quantity=Decimal("6.6"),
                is_active=True,
            )
            session.add(link)
            await session.flush()
            session.add(
                PricePoint(
                    product_store_id=link.id,
                    price_sek=Decimal("137.45"),
                    checked_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
        await session.flush()
        return product

    @pytest.mark.asyncio
    async def test_only_the_offers_own_store_is_notable(self, db_session) -> None:
        await self._product_with_links(db_session, "Läsk Cola Zero 33cl", ["willys", "ica"])

        offer = parse_offline_offer("2500310468", _payload())
        assert offer is not None
        candidates = await match_candidates(db_session, offer)

        assert len(candidates) == 1
        links = candidates[0]["notable_links"]
        assert [link["package_size"] for link in links] == ["willys-pack"]
        assert links[0]["store_name"] == "Willys"
        # The link's current price rides along so the note can prefill ordinarie.
        assert links[0]["price_sek"] == pytest.approx(137.45)

    @pytest.mark.asyncio
    async def test_a_product_tracked_elsewhere_only_has_no_notable_link(
        self, db_session
    ) -> None:
        """An honest empty list: the portal says "ingen Willys-länk att notera på"
        rather than offering a target that would misattribute the observation."""
        await self._product_with_links(db_session, "Läsk Cola Zero 33cl", ["ica"])

        offer = parse_offline_offer("2500310468", _payload())
        assert offer is not None
        candidates = await match_candidates(db_session, offer)

        assert len(candidates) == 1
        assert candidates[0]["notable_links"] == []
