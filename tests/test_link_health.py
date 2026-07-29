"""domain/link_health.py — THE judgement of a broken link.

The pure tests pin the rules (threshold, self-clearing, newest-first); the Postgres tier
proves the windowed query feeds them right — including that blocked attempts are invisible
in BOTH directions, which is what keeps an ICA wall from spraying "trasig länk" across a
store full of valid links.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from domain.link_health import (
    BROKEN_AFTER_ATTEMPTS,
    LinkHealth,
    broken_from_attempts,
    broken_links,
)
from domain.models import CheckAttempt, Product, ProductStore, Store
from domain.tenant import DEFAULT_TENANT_ID

LINK = uuid.uuid4()
OTHER = uuid.uuid4()


def _fails(n: int, link: uuid.UUID = LINK, detail: str | None = "HTTP 404"):
    return [(link, "fetch_failed", detail)] * n


class TestBrokenFromAttempts:
    def test_threshold_of_consecutive_failures_marks_broken(self):
        broken = broken_from_attempts(_fails(BROKEN_AFTER_ATTEMPTS))
        assert broken == {LINK: LinkHealth(last_outcome="fetch_failed", last_detail="HTTP 404")}

    def test_fewer_than_threshold_is_a_bad_day_not_broken(self):
        assert broken_from_attempts(_fails(BROKEN_AFTER_ATTEMPTS - 1)) == {}

    def test_one_ok_among_the_recent_clears_the_flag(self):
        rows = _fails(1) + [(LINK, "ok", None)] + _fails(2)
        assert broken_from_attempts(rows) == {}

    def test_an_ok_older_than_the_streak_does_not_save_the_link(self):
        """Newest first: three fresh failures ARE broken even if the link once worked."""
        rows = _fails(BROKEN_AFTER_ATTEMPTS) + [(LINK, "ok", None)]
        assert LINK in broken_from_attempts(rows)

    def test_detail_comes_from_the_newest_attempt(self):
        rows = [(LINK, "no_price", None), *_fails(2)]
        health = broken_from_attempts(rows)[LINK]
        assert health.last_outcome == "no_price"
        assert health.last_detail is None

    def test_error_outcome_counts_as_failure(self):
        rows = [(LINK, "error", "boom")] * BROKEN_AFTER_ATTEMPTS
        assert LINK in broken_from_attempts(rows)

    def test_links_are_judged_independently(self):
        rows = _fails(BROKEN_AFTER_ATTEMPTS) + _fails(1, link=OTHER)
        broken = broken_from_attempts(rows)
        assert LINK in broken and OTHER not in broken


@pytest.mark.integration
class TestBrokenLinksQuery:
    """Real Postgres: the windowed query, and blocked-invisibility in both directions."""

    async def _link(self, session) -> ProductStore:
        store = (await session.execute(select(Store).where(Store.slug == "apotea"))).scalar_one()
        product = Product(
            tenant_id=DEFAULT_TENANT_ID, name="Lambi", brand="Lambi", category=None, unit="st"
        )
        session.add(product)
        await session.flush()
        link = ProductStore(
            product_id=product.id,
            store_id=store.id,
            store_url=f"https://www.apotea.se/{uuid.uuid4()}",
            package_quantity=Decimal("24"),
        )
        session.add(link)
        await session.flush()
        return link

    def _attempt(self, link: ProductStore, minutes_ago: int, outcome: str, detail=None):
        return CheckAttempt(
            store_id=link.store_id,
            product_store_id=link.id,
            checked_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago),
            outcome=outcome,
            source="scheduler",
            detail=detail,
        )

    @pytest.mark.asyncio
    async def test_three_recent_failures_flag_the_link_with_the_latest_detail(
        self, db_session
    ) -> None:
        link = await self._link(db_session)
        db_session.add_all(
            [
                self._attempt(link, 300, "ok"),  # it worked once — long ago
                self._attempt(link, 30, "fetch_failed", "HTTP 404"),
                self._attempt(link, 20, "fetch_failed", "HTTP 404"),
                self._attempt(link, 10, "no_price"),
            ]
        )
        await db_session.flush()

        broken = await broken_links(db_session, [link.id])
        assert broken == {link.id: LinkHealth(last_outcome="no_price", last_detail=None)}

    @pytest.mark.asyncio
    async def test_blocked_attempts_neither_count_nor_interrupt(self, db_session) -> None:
        """A wall between two 404s must not reset the streak — AND three walls alone must
        never read as a broken link. Both directions of v0.29.2's 'a wall is the store's
        state', or an ICA challenge evening marks every ICA link broken."""
        walled = await self._link(db_session)
        db_session.add_all(
            [self._attempt(walled, 40 - i * 10, "blocked", "blocked (HTTP 202)") for i in range(3)]
        )
        interrupted = await self._link(db_session)
        db_session.add_all(
            [
                self._attempt(interrupted, 50, "fetch_failed", "HTTP 404"),
                self._attempt(interrupted, 40, "blocked", "blocked (HTTP 202)"),
                self._attempt(interrupted, 30, "fetch_failed", "HTTP 404"),
                self._attempt(interrupted, 10, "fetch_failed", "HTTP 404"),
            ]
        )
        await db_session.flush()

        broken = await broken_links(db_session, [walled.id, interrupted.id])
        assert walled.id not in broken
        assert interrupted.id in broken

    @pytest.mark.asyncio
    async def test_a_recovered_link_is_not_broken(self, db_session) -> None:
        link = await self._link(db_session)
        db_session.add_all(
            [
                self._attempt(link, 40, "fetch_failed", "HTTP 503"),
                self._attempt(link, 30, "fetch_failed", "HTTP 503"),
                self._attempt(link, 20, "fetch_failed", "HTTP 503"),
                self._attempt(link, 10, "ok"),
            ]
        )
        await db_session.flush()

        assert await broken_links(db_session, [link.id]) == {}

    @pytest.mark.asyncio
    async def test_no_ids_means_no_query(self, db_session) -> None:
        assert await broken_links(db_session, []) == {}
