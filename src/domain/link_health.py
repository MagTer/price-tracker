"""THE definition of a broken link — a URL the store keeps answering "no" about.

``check_attempts`` (v0.35.0) records what the stores refused to say; this module is the
one reader that turns that record into the UI's "trasig länk" signal. Never write a
second judgement — the facet on Produkter and the badge in the links panel must agree,
and so must anything added later. The judgement:

- Broken = the link's last ``BROKEN_AFTER_ATTEMPTS`` non-blocked attempts ALL failed
  (outcome != "ok"), and at least that many exist. Fewer failures is a bad day, not a
  broken link — one transient 5xx or one unlucky parse must not flag a working URL,
  and since v0.39.0 a failed weekday link retries every morning, so a genuinely dead
  URL crosses the threshold within days on its own.
- BLOCKED attempts are invisible here, in both directions: a wall is the STORE's state
  (v0.29.2), so it neither counts toward broken nor interrupts a failure streak.
  Without this an ICA wall would spray "trasig länk" across every valid ICA link —
  inviting exactly the wrong response (deleting them), the same misread that made
  v0.19.0 stop verifying sibling links inline.
- One "ok" clears the flag by construction. Nothing is persisted, so there is nothing
  to dismiss — the same self-clearing shape as ``quantity_mismatch``.

Deliberately NOT here: automatic retirement of broken links, and any "repoint the URL"
affordance. The flag is a work list for the operator; the actions stay where they live
(Kontrollera nu, Ta bort länk in the edit dialog), and a link's URL is its identity —
the fix for a moved page is a new link via quick-add, never re-pointing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from domain.models import CheckAttempt

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Three, because it is the smallest streak that cannot be produced by one transient
# hiccup plus one coincidence — and with the daily morning retry it still surfaces a
# dead URL within half a week.
BROKEN_AFTER_ATTEMPTS = 3


@dataclass(frozen=True)
class LinkHealth:
    """What the most recent failed attempt said — the badge's tooltip material."""

    last_outcome: str  # "fetch_failed" | "no_price" | "error"
    last_detail: str | None  # e.g. "HTTP 404"; the wire's broken_detail


def broken_from_attempts(
    rows: Iterable[tuple[uuid.UUID, str, str | None]],
    threshold: int = BROKEN_AFTER_ATTEMPTS,
) -> dict[uuid.UUID, LinkHealth]:
    """The broken links among ``rows`` — (link id, outcome, detail), newest first per link.

    Pure so the judgement is testable without a database; callers feed it rows already
    filtered of blocked attempts (see ``broken_links``, the one query). Rows beyond the
    threshold per link are ignored, so passing more history than needed is harmless.
    """
    by_link: dict[uuid.UUID, list[tuple[str, str | None]]] = {}
    for ps_id, outcome, detail in rows:
        by_link.setdefault(ps_id, []).append((outcome, detail))

    broken: dict[uuid.UUID, LinkHealth] = {}
    for ps_id, attempts in by_link.items():
        recent = attempts[:threshold]
        if len(recent) >= threshold and all(outcome != "ok" for outcome, _ in recent):
            broken[ps_id] = LinkHealth(last_outcome=recent[0][0], last_detail=recent[0][1])
    return broken


async def broken_links(
    session: AsyncSession, product_store_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, LinkHealth]:
    """THE broken-link lookup: link id → health, for every broken link among the given ids.

    One windowed query regardless of link count — the read paths batch their links
    already, and a per-link query would be the N+1 they were built to avoid.
    """
    ids = list(product_store_ids)
    if not ids:
        return {}

    rn = (
        func.row_number()
        .over(
            partition_by=CheckAttempt.product_store_id,
            order_by=CheckAttempt.checked_at.desc(),
        )
        .label("rn")
    )
    recent = (
        select(CheckAttempt.product_store_id, CheckAttempt.outcome, CheckAttempt.detail, rn)
        .where(
            CheckAttempt.product_store_id.in_(ids),
            CheckAttempt.outcome != "blocked",
        )
        .subquery()
    )
    result = await session.execute(
        select(recent.c.product_store_id, recent.c.outcome, recent.c.detail)
        .where(recent.c.rn <= BROKEN_AFTER_ATTEMPTS)
        .order_by(recent.c.product_store_id, recent.c.rn)
    )
    return broken_from_attempts(result.all())
