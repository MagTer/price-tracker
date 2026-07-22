"""Tests for domain/schedule.py — store defaults, link inheritance, next-check time.

The schedule module is THE definition (scheduler and admin API both call it), so these
tests pin the semantics: wholesale override, förmiddag alignment, never-today weekdays.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from domain.schedule import (
    MORNING_END_HOUR,
    MORNING_START_HOUR,
    effective_schedule,
    is_inherited,
    next_check_time,
)


@dataclass
class _Carrier:
    check_weekdays: list[int] | None = None
    check_frequency_hours: int | None = None


# A Tuesday, afternoon — weekday arithmetic below is relative to this.
TUESDAY = datetime(2026, 7, 21, 15, 30, 0)
assert TUESDAY.weekday() == 1


class TestEffectiveSchedule:
    def test_inherits_store_schedule_when_link_is_blank(self):
        link = _Carrier()
        store = _Carrier(check_weekdays=[0, 4], check_frequency_hours=72)
        assert is_inherited(link)
        assert effective_schedule(link, store) == ([0, 4], 72)

    def test_inherits_interval_store(self):
        link = _Carrier()
        store = _Carrier(check_weekdays=None, check_frequency_hours=72)
        assert effective_schedule(link, store) == ([], 72)

    def test_link_weekdays_override_wins(self):
        link = _Carrier(check_weekdays=[2])
        store = _Carrier(check_weekdays=[0], check_frequency_hours=72)
        assert not is_inherited(link)
        assert effective_schedule(link, store) == ([2], 72)

    def test_link_interval_override_beats_store_weekdays(self):
        """The override is WHOLESALE: an interval-only link at a weekday store is
        interval mode, not 'store's Mondays at the link's frequency'."""
        link = _Carrier(check_frequency_hours=96)
        store = _Carrier(check_weekdays=[0], check_frequency_hours=72)
        assert effective_schedule(link, store) == ([], 96)

    def test_weekdays_are_deduped_and_sorted(self):
        link = _Carrier(check_weekdays=[4, 0, 4])
        store = _Carrier(check_frequency_hours=72)
        assert effective_schedule(link, store) == ([0, 4], 72)

    def test_frequency_falls_back_store_then_default(self):
        # Weekday link without own frequency: store's frequency fills the pair.
        link = _Carrier(check_weekdays=[0])
        assert effective_schedule(link, _Carrier(check_frequency_hours=96)) == ([0], 96)
        # No frequency anywhere (unflushed ORM objects): the module default holds.
        assert effective_schedule(_Carrier(), _Carrier()) == ([], 72)


class TestNextCheckTime:
    def test_single_weekday_lands_on_that_day_in_the_morning(self):
        nxt = next_check_time([0], 72, TUESDAY)  # Monday schedule, from a Tuesday
        assert nxt.weekday() == 0
        assert (nxt.date() - TUESDAY.date()).days == 6
        assert MORNING_START_HOUR <= nxt.hour < MORNING_END_HOUR

    def test_multiple_weekdays_pick_the_nearest(self):
        nxt = next_check_time([0, 4], 72, TUESDAY)  # Mon+Fri schedule, from a Tuesday
        assert nxt.weekday() == 4  # Friday is 3 days away, Monday 6
        assert (nxt.date() - TUESDAY.date()).days == 3

    def test_same_day_schedules_next_occurrence_not_today(self):
        """A check that just ran counts as today's — Monday on a Monday means +7 days."""
        monday = TUESDAY - timedelta(days=1)
        assert monday.weekday() == 0
        nxt = next_check_time([0], 72, monday)
        assert nxt.weekday() == 0
        assert (nxt.date() - monday.date()).days == 7

    def test_interval_snaps_to_the_morning_window(self):
        """72h from a Tuesday afternoon is a Friday afternoon — the snap moves it to
        Friday FÖRMIDDAG, so interval checks land when weekday checks do."""
        nxt = next_check_time([], 72, TUESDAY)
        assert MORNING_START_HOUR <= nxt.hour < MORNING_END_HOUR
        # ±10 % jitter on 72h moves the target ±7.2h around Friday 15:30 — every
        # outcome stays on Friday, so the DATE is deterministic even if the hour isn't.
        assert (nxt.date() - TUESDAY.date()).days == 3

    def test_subday_interval_keeps_exact_spacing(self):
        """Sub-day intervals are NOT morning-snapped — snapping would collapse several
        checks onto one morning."""
        nxt = next_check_time([], 6, TUESDAY)
        delta_hours = (nxt - TUESDAY).total_seconds() / 3600
        assert 6 * 0.9 <= delta_hours <= 6 * 1.1

    def test_interval_jitter_stays_within_ten_percent(self):
        for _ in range(20):
            nxt = next_check_time([], 96, TUESDAY)
            delta_hours = (nxt - TUESDAY).total_seconds() / 3600
            # Morning snap can move the moment within the target day, so allow the
            # window that jitter plus a same-day snap can produce.
            assert 96 - 9.6 - 12 <= delta_hours <= 96 + 9.6 + 12
