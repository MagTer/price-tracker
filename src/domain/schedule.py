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

Scheduler and admin API must BOTH compute next-check through this module — the admin
endpoint's former private copy of the weekday arithmetic is exactly the Gotcha-4 drift
pattern this file exists to prevent. Never write a second definition.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Protocol

DEFAULT_FREQUENCY_HOURS = 72

MORNING_START_HOUR = 6
MORNING_END_HOUR = 12


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


def next_check_time(weekdays: list[int], frequency_hours: int, now: datetime) -> datetime:
    """Next check for a schedule, from a naive-UTC ``now``.

    Weekday mode: the nearest listed weekday, never today — a check that just ran counts
    as today's, so a Monday check on a Monday schedules next Monday. Interval mode:
    now + frequency ±10 % jitter, then snapped to that day's förmiddag when the interval
    spans at least a day (sub-day intervals keep their exact spacing — snapping them
    would collapse several checks onto one morning).
    """
    if weekdays:
        days_until = min(((d - now.weekday()) % 7) or 7 for d in weekdays)
        return _at_morning(now + timedelta(days=days_until))

    jitter = (random.random() * 2 - 1) * 0.1 * frequency_hours  # noqa: S311
    target = now + timedelta(hours=frequency_hours + jitter)
    if frequency_hours >= 24:
        target = _at_morning(target)
    return target


def _at_morning(day: datetime) -> datetime:
    """The same date at a random time inside the förmiddag window."""
    hour = MORNING_START_HOUR + int(
        random.random() * (MORNING_END_HOUR - MORNING_START_HOUR)  # noqa: S311
    )
    minute = int(random.random() * 60)  # noqa: S311
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)
