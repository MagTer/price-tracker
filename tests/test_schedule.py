"""Tests for domain/schedule.py — store defaults, link inheritance, next-check time.

The schedule module is THE definition (scheduler and admin API both call it), so these
tests pin the semantics: wholesale override, förmiddag alignment, never-today weekdays —
and since v0.33.0, that förmiddag and those weekdays are Europe/Stockholm wall-clock
concepts computed from (and returned as) naive-UTC instants.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from domain.schedule import (
    MORNING_END_HOUR,
    MORNING_START_HOUR,
    STORE_TIMEZONE,
    effective_schedule,
    is_inherited,
    next_check_time,
)


@dataclass
class _Carrier:
    check_weekdays: list[int] | None = None
    check_frequency_hours: int | None = None


def stockholm(naive_utc: datetime) -> datetime:
    """A result (naive UTC by convention) as Swedish wall-clock, for asserting on."""
    return naive_utc.replace(tzinfo=UTC).astimezone(STORE_TIMEZONE)


def in_morning_window(naive_utc: datetime) -> bool:
    return MORNING_START_HOUR <= stockholm(naive_utc).hour < MORNING_END_HOUR


# A Tuesday, afternoon, in July — naive UTC like every timestamp in the app; Swedish
# time is UTC+2 (CEST) here. Weekday arithmetic below is relative to this.
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
        assert stockholm(nxt).weekday() == 0
        assert (stockholm(nxt).date() - TUESDAY.date()).days == 6
        assert in_morning_window(nxt)

    def test_multiple_weekdays_pick_the_nearest(self):
        nxt = next_check_time([0, 4], 72, TUESDAY)  # Mon+Fri schedule, from a Tuesday
        assert stockholm(nxt).weekday() == 4  # Friday is 3 days away, Monday 6
        assert (stockholm(nxt).date() - TUESDAY.date()).days == 3

    def test_same_day_schedules_next_occurrence_not_today(self):
        """A check that just ran counts as today's — Monday on a Monday means +7 days."""
        monday = TUESDAY - timedelta(days=1)
        assert monday.weekday() == 0
        nxt = next_check_time([0], 72, monday)
        assert stockholm(nxt).weekday() == 0
        assert (stockholm(nxt).date() - monday.date()).days == 7

    def test_interval_snaps_to_the_morning_window(self):
        """72h from a Tuesday afternoon is a Friday afternoon — the snap moves it to
        Friday FÖRMIDDAG, so interval checks land when weekday checks do."""
        nxt = next_check_time([], 72, TUESDAY)
        assert in_morning_window(nxt)
        # ±10 % jitter on 72h moves the target ±7.2h around Friday 17:30 SWEDISH time
        # (15:30 UTC) — positive jitter can cross local midnight into Saturday, so the
        # snapped date is Friday or Saturday. (The old UTC-computed window had two more
        # hours of slack before the date rolled; the local window is simply honest.)
        assert (stockholm(nxt).date() - TUESDAY.date()).days in (3, 4)

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


class TestSwedishWallClock:
    """The förmiddag window and the weekday are Europe/Stockholm concepts (v0.33.0).

    Before this, both were computed straight on naive UTC: "06–12" silently meant 08–14
    Swedish summer time (07–13 winter, drifting an hour at each DST change), and the
    weekday was judged by a clock whose Monday starts at 02:00 Swedish summer time.
    """

    def test_summer_window_is_04_to_10_utc(self):
        """CEST (UTC+2): Swedish 06–12 must come back as 04–10 in the stored UTC."""
        for _ in range(10):
            nxt = next_check_time([0], 72, TUESDAY)
            assert 4 <= nxt.hour < 10
            assert in_morning_window(nxt)

    def test_winter_window_is_05_to_11_utc(self):
        """CET (UTC+1): same Swedish förmiddag, one UTC hour later than in summer —
        the drift the old UTC-computed window could not express."""
        winter_tuesday = datetime(2026, 1, 20, 15, 30, 0)
        assert winter_tuesday.weekday() == 1
        for _ in range(10):
            nxt = next_check_time([0], 72, winter_tuesday)
            assert 5 <= nxt.hour < 11
            assert in_morning_window(nxt)

    def test_weekday_is_judged_by_the_swedish_clock_at_midnight(self):
        """Sunday 22:30 UTC in July IS Monday 00:30 in Sweden. A Monday schedule must
        treat it as "Monday's check already happened" and go to NEXT Monday — the UTC
        clock would say Sunday and schedule the very next morning instead. This is the
        case that matters if the ICA window ever moves to just after midnight."""
        sunday_2230_utc = datetime(2026, 7, 26, 22, 30, 0)
        assert sunday_2230_utc.weekday() == 6  # UTC says Sunday...
        assert stockholm(sunday_2230_utc).weekday() == 0  # ...Sweden says Monday

        nxt = next_check_time([0], 72, sunday_2230_utc)

        assert stockholm(nxt).weekday() == 0
        assert (stockholm(nxt).date() - stockholm(sunday_2230_utc).date()).days == 7

    def test_interval_snap_lands_in_the_swedish_window_in_winter(self):
        winter_tuesday = datetime(2026, 1, 20, 15, 30, 0)
        nxt = next_check_time([], 72, winter_tuesday)
        assert in_morning_window(nxt)
