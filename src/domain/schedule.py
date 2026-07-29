"""THE definition of check scheduling — store defaults, link inheritance, next-check time.

The schedule is a STORE property first (politeness toward the site and the chain's offer
cycle are chain facts: ICA publishes Mondays, Willys Mondays and Fridays, pharmacies have
no cycle), and a LINK property only as an explicit override (a watched product can earn a
tighter cadence). A link with neither field set inherits the store's schedule — that is
the normal state, and quick-add creates links that way.

Two modes, both landing on förmiddag (06:00–12:00) because that is when Swedish chains
have published the day's changes and traffic is human-plausible:

- weekday mode (``check_weekdays`` non-empty): one check per listed weekday.
- interval mode (no weekdays): every ``check_frequency_hours`` (±10 % jitter), snapped to
  the target day's förmiddag when the interval is a day or longer.

**Civil time is Europe/Stockholm; storage stays naive UTC (v0.33.0).** "Förmiddag" and
"måndag" are Swedish wall-clock concepts — the chains publish on Swedish mornings — so
the window and the weekday are computed in ``STORE_TIMEZONE`` and only then converted
back to the app-wide naive-UTC convention. Computing them directly on UTC (the pre-
v0.33.0 behaviour) silently meant 08–14 Swedish summer time (07–13 winter), drifting an
hour at every DST change, and judged "which weekday is it" by a clock whose Monday
starts at 02:00 Swedish summer time — wrong night for any window near midnight.

Scheduler and admin API must BOTH compute next-check through this module — the admin
endpoint's former private copy of the weekday arithmetic is exactly the Gotcha-4 drift
pattern this file exists to prevent. Never write a second definition.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_FREQUENCY_HOURS = 72

# Local (Europe/Stockholm) hours. The DST transitions happen 02:00–03:00, never inside
# this window, so setting these hours on a local date is never ambiguous or missing.
MORNING_START_HOUR = 6
MORNING_END_HOUR = 12

STORE_TIMEZONE = ZoneInfo("Europe/Stockholm")


def _local(naive_utc: datetime) -> datetime:
    """A naive-UTC instant as an aware Europe/Stockholm datetime."""
    return naive_utc.replace(tzinfo=UTC).astimezone(STORE_TIMEZONE)


def _naive_utc(local: datetime) -> datetime:
    """An aware local datetime back to the app-wide naive-UTC storage convention."""
    return local.astimezone(UTC).replace(tzinfo=None)


class _ScheduleCarrier(Protocol):
    """The two schedule columns shared by Store (defaults) and ProductStore (override)."""

    check_weekdays: list[int] | None
    check_frequency_hours: int | None


def is_inherited(link: _ScheduleCarrier) -> bool:
    """True when the link carries no schedule of its own and follows its store."""
    return link.check_weekdays is None and link.check_frequency_hours is None


def effective_schedule(link: _ScheduleCarrier, store: _ScheduleCarrier) -> tuple[list[int], int]:
    """The (weekdays, frequency_hours) actually in force for a link.

    The override is WHOLESALE: if the link sets either field, the link's pair defines the
    schedule (missing frequency falls back to the store's, since weekday mode does not use
    it). Per-field mixing would make "link says every 96h, store says Mondays" ambiguous.
    """
    if not is_inherited(link):
        weekdays = link.check_weekdays or []
        frequency = (
            link.check_frequency_hours or store.check_frequency_hours or DEFAULT_FREQUENCY_HOURS
        )
    else:
        weekdays = store.check_weekdays or []
        frequency = store.check_frequency_hours or DEFAULT_FREQUENCY_HOURS
    return sorted(set(weekdays)), frequency


def next_check_time(
    weekdays: list[int],
    frequency_hours: int,
    now: datetime,
    slot: tuple[int, int] | None = None,
) -> datetime:
    """Next check for a schedule, from a naive-UTC ``now``. Returns naive UTC.

    Weekday mode: the nearest listed weekday **in Swedish local time**, never today — a
    check that just ran counts as today's, so a Monday check on a Monday schedules next
    Monday (and a check at 00:30 Swedish Monday night IS on a Monday, even though UTC
    still says Sunday). Interval mode: now + frequency ±10 % jitter — instant arithmetic,
    timezone-free — then snapped to the target's **local** förmiddag when the interval
    spans at least a day (sub-day intervals keep their exact spacing — snapping them
    would collapse several checks onto one morning).

    ``slot`` is the link's (index, count) among the store's links landing on the same
    weekday — see ``weekday_slot``. When given, the time inside the förmiddag window is
    stratified instead of independently random: measured in prod, 56 free draws over the
    window put 4 pairs on the same minute and left 28-minute holes, and same-minute due
    times drain through the rate limiter as the back-to-back burst pattern that trips a
    WAF. Callers that cannot know the siblings (interval mode, retries) pass None and get
    the free draw.
    """
    if weekdays:
        local_now = _local(now)
        target_weekday = _nearest_weekday(weekdays, local_now)
        days_until = ((target_weekday - local_now.weekday()) % 7) or 7
        return _naive_utc(_at_morning(local_now + timedelta(days=days_until), slot))

    jitter = (random.random() * 2 - 1) * 0.1 * frequency_hours  # noqa: S311
    target = now + timedelta(hours=frequency_hours + jitter)
    if frequency_hours >= 24:
        target = _naive_utc(_at_morning(_local(target)))
    return target


def next_morning_retry(now: datetime) -> datetime:
    """Tomorrow's Swedish förmiddag, from a naive-UTC ``now``. Returns naive UTC.

    The retry for a failed weekday-scheduled check: sooner than waiting out the week, but
    never bare ``now + 24h`` — that keeps the clock time the failure happened at, so a
    check that failed at 12:01 local retried at 12:01 forever, outside the window and on
    whatever weekday the drift landed on (the same walk v0.29.2 stopped for blocks).
    """
    return _naive_utc(_at_morning(_local(now) + timedelta(days=1)))


def _nearest_weekday(weekdays: list[int], local_now: datetime) -> int:
    """The listed weekday the next check lands on (Swedish local), never today."""
    return min(weekdays, key=lambda d: ((d - local_now.weekday()) % 7) or 7)


def _at_morning(local_day: datetime, slot: tuple[int, int] | None = None) -> datetime:
    """The same LOCAL date at a time inside the förmiddag window (aware in/out).

    With a slot (index i of n): the stratified position ``start + (i + 0.5) × window/n``,
    jittered by ±25 % of the stratum width — enough that the pattern is not machine-exact,
    while adjacent links stay at least half a stratum apart and no jitter can escape the
    window (centre offset ≥ 0.5×width from either edge, jitter < 0.5×width). Without a
    slot: a free uniform draw. Both carry second resolution — the old whole-minute grid
    was itself a collision source (360 slots for 56 links).
    """
    window_seconds = (MORNING_END_HOUR - MORNING_START_HOUR) * 3600
    if slot is not None and 0 <= slot[0] < slot[1]:
        index, count = slot
        width = window_seconds / count
        offset = (index + 0.5) * width + (random.random() * 2 - 1) * 0.25 * width  # noqa: S311
    else:
        offset = random.random() * window_seconds  # noqa: S311
    return local_day.replace(
        hour=MORNING_START_HOUR, minute=0, second=0, microsecond=0
    ) + timedelta(seconds=int(offset))


# Carries one sibling row of the store through effective_schedule without loading the ORM
# entity — weekday_slot reads three columns off up to ~60 rows, nothing more.
@dataclass
class _SiblingSchedule:
    check_weekdays: list[int] | None
    check_frequency_hours: int | None


async def weekday_slot(
    session: AsyncSession,
    link_id: uuid.UUID,
    store_id: uuid.UUID,
    store: _ScheduleCarrier,
    weekdays: list[int],
    now: datetime,
) -> tuple[int, int] | None:
    """The link's (index, count) among the store's links landing on the same weekday.

    THE sibling lookup — scheduler and admin API both resolve stratification through this
    one function (via next_check_time_for_link), because the slot depends on which OTHER
    links share the target weekday and two private copies of that query would drift.
    Siblings are the store's active links whose effective schedule (link override, else
    store default) includes the weekday the next check lands on; ordering is the link id,
    which is arbitrary but stable, so a link keeps roughly the same position week to week.
    Returns None when the link is not among its own siblings (not yet flushed, just
    deactivated) — the caller falls back to the unstratified draw rather than guessing.
    """
    from sqlalchemy import select

    from domain.models import ProductStore

    target_weekday = _nearest_weekday(weekdays, _local(now))
    result = await session.execute(
        select(
            ProductStore.id, ProductStore.check_weekdays, ProductStore.check_frequency_hours
        ).where(ProductStore.store_id == store_id, ProductStore.is_active.is_(True))
    )
    siblings = [
        row_id
        for row_id, link_weekdays, link_frequency in result.all()
        if target_weekday
        in effective_schedule(_SiblingSchedule(link_weekdays, link_frequency), store)[0]
    ]
    siblings.sort(key=str)
    if link_id not in siblings:
        return None
    return siblings.index(link_id), len(siblings)


class _LinkCarrier(_ScheduleCarrier, Protocol):
    """A ProductStore as next_check_time_for_link needs it: schedule columns + identity."""

    id: uuid.UUID
    store_id: uuid.UUID


async def next_check_time_for_link(
    session: AsyncSession, link: _LinkCarrier, store: _ScheduleCarrier, now: datetime
) -> datetime:
    """Resolve a link's schedule, stratified slot and next-check time in one call.

    THE entry point for rescheduling a link after a completed check — the scheduler and
    the admin ``/frequency`` endpoint both go through here, so slot resolution cannot
    exist in one caller and not the other.
    """
    weekdays, frequency = effective_schedule(link, store)
    slot = None
    if weekdays:
        slot = await weekday_slot(session, link.id, link.store_id, store, weekdays, now)
    return next_check_time(weekdays, frequency, now, slot=slot)
