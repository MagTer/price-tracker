"""Tests for domain/schedule.py — store defaults, link inheritance, next-check time.

The schedule module is THE definition (scheduler and admin API both call it), so these
tests pin the semantics: wholesale override, förmiddag alignment, never-today weekdays —
and since v0.33.0, that förmiddag and those weekdays are Europe/Stockholm wall-clock
concepts computed from (and returned as) naive-UTC instants.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.schedule import (
    MORNING_END_HOUR,
    MORNING_START_HOUR,
    STORE_TIMEZONE,
    effective_schedule,
    is_inherited,
    next_check_time,
    next_morning_retry,
    weekday_slot,
    weekly_summary_slot,
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


class TestStratifiedSlots:
    """Slot-stratified förmiddag placement (v0.39.0).

    A free random draw per link is uniform in expectation but lumpy in practice —
    measured in prod, 56 ICA links put 4 pairs on the same minute and left 28-minute
    holes, and same-minute due times drain through the rate limiter back to back, the
    burst pattern that trips a WAF. These tests pin the SPREAD as a property (minimum
    gap, no duplicates, window containment), never exact timestamps.
    """

    def test_full_store_of_slots_spreads_with_a_minimum_gap(self):
        random.seed(20260729)
        n = 56  # prod's ICA link count
        times = [next_check_time([0], 72, TUESDAY, slot=(i, n)) for i in range(n)]

        assert all(in_morning_window(t) for t in times)
        assert len(set(times)) == n  # no two links on the same second
        assert times == sorted(times)  # slot order is time order — jitter cannot reorder
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
        # Strata are window/n ≈ 386 s apart with jitter ±25 % of a stratum, so adjacent
        # links are guaranteed at least half a stratum apart — well clear of the 90 s
        # collision band the Monte Carlo counted.
        assert min(gaps) >= 0.5 * (6 * 3600 / n)

    def test_edge_slots_never_leave_the_window(self):
        """The first and last stratum, jitter included, stay inside 06–12 local."""
        random.seed(20260729)
        for n in (1, 2, 7, 56):
            for _ in range(50):
                first = next_check_time([0], 72, TUESDAY, slot=(0, n))
                last = next_check_time([0], 72, TUESDAY, slot=(n - 1, n))
                assert in_morning_window(first)
                assert in_morning_window(last)

    def test_same_slot_is_jittered_between_draws(self):
        """The stratum centre is jittered — a machine-exact grid is itself a bot tell."""
        random.seed(20260729)
        draws = {next_check_time([0], 72, TUESDAY, slot=(3, 56)) for _ in range(20)}
        assert len(draws) > 1

    def test_malformed_slot_falls_back_to_the_free_draw(self):
        """(index, count) outside the valid range must not crash or escape the window."""
        random.seed(20260729)
        for slot in ((5, 3), (-1, 3), (0, 0)):
            nxt = next_check_time([0], 72, TUESDAY, slot=slot)
            assert in_morning_window(nxt)

    def test_unslotted_draw_uses_second_resolution(self):
        """The old whole-minute grid (second=0) was 360 slots for 56 links — a collision
        source in itself. The free draw now lands on any second."""
        random.seed(20260729)
        draws = [next_check_time([0], 72, TUESDAY) for _ in range(50)]
        assert any(t.second != 0 for t in draws)
        assert all(in_morning_window(t) for t in draws)


def _slot_session(rows: list[tuple]) -> AsyncMock:
    """A mocked AsyncSession whose single execute returns (id, weekdays, freq) rows."""
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


class TestWeekdaySlot:
    """THE sibling lookup — which links share the target weekday, and in what order."""

    STORE = _Carrier(check_weekdays=[0], check_frequency_hours=None)

    @pytest.mark.asyncio
    async def test_counts_and_orders_inheriting_siblings(self):
        ids = sorted([uuid.uuid4() for _ in range(3)], key=str)
        session = _slot_session([(i, None, None) for i in ids])

        for expected_index, link_id in enumerate(ids):
            slot = await weekday_slot(session, link_id, uuid.uuid4(), self.STORE, [0], TUESDAY)
            assert slot == (expected_index, 3)

    @pytest.mark.asyncio
    async def test_excludes_siblings_on_another_schedule(self):
        """An interval-mode override and a different-weekday override are not siblings on
        Monday — counting them would leave permanent holes in the Monday grid."""
        mine = uuid.uuid4()
        session = _slot_session(
            [
                (mine, None, None),  # inherits the store's Mondays
                (uuid.uuid4(), None, 96),  # interval override — never on the weekday grid
                (uuid.uuid4(), [2], None),  # Wednesday override — a different grid
            ]
        )

        slot = await weekday_slot(session, mine, uuid.uuid4(), self.STORE, [0], TUESDAY)
        assert slot == (0, 1)

    @pytest.mark.asyncio
    async def test_slot_keys_on_the_weekday_the_check_lands_on(self):
        """Mon+Fri from a Tuesday lands on Friday: a Friday-only sibling counts, a
        Monday-only sibling does not."""
        mine = uuid.uuid4()
        friday_sibling = uuid.uuid4()
        session = _slot_session(
            [
                (mine, [0, 4], None),
                (friday_sibling, [4], None),
                (uuid.uuid4(), [0], None),  # Monday-only — not on Friday's grid
            ]
        )

        slot = await weekday_slot(session, mine, uuid.uuid4(), self.STORE, [0, 4], TUESDAY)
        assert slot is not None
        assert slot[1] == 2  # mine + the Friday sibling

    @pytest.mark.asyncio
    async def test_link_missing_from_its_own_siblings_yields_none(self):
        """Not-yet-flushed or just-deactivated: fall back to the free draw, don't guess."""
        session = _slot_session([(uuid.uuid4(), None, None)])
        slot = await weekday_slot(session, uuid.uuid4(), uuid.uuid4(), self.STORE, [0], TUESDAY)
        assert slot is None


class TestNextMorningRetry:
    """A failed weekday check retries tomorrow FÖRMIDDAG — never bare +24h, which keeps
    the clock time the failure happened at and walks the link out of the window."""

    def test_failure_after_the_window_lands_in_next_days_window(self):
        # The prod case: a 404 at 12:01 local (10:01 UTC in July) stood at 12:01 forever.
        failed_at = datetime(2026, 7, 29, 10, 1, 0)
        nxt = next_morning_retry(failed_at)
        assert in_morning_window(nxt)
        assert stockholm(nxt).date() == stockholm(failed_at).date() + timedelta(days=1)

    def test_late_night_failure_respects_the_swedish_date_line(self):
        """23:16 UTC in July is 01:16 SWEDISH the next day — "tomorrow" is judged by the
        Swedish clock, so the retry lands the day after the Swedish date."""
        failed_at = datetime(2026, 7, 28, 23, 16, 0)
        nxt = next_morning_retry(failed_at)
        assert in_morning_window(nxt)
        assert stockholm(nxt).date() == stockholm(failed_at).date() + timedelta(days=1)

    def test_winter_retry_stays_in_the_swedish_window(self):
        failed_at = datetime(2026, 1, 20, 13, 1, 0)
        for _ in range(10):
            nxt = next_morning_retry(failed_at)
            assert in_morning_window(nxt)
            assert 5 <= nxt.hour < 11  # CET: Swedish 06–12 is 05–11 UTC


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

    def test_next_monday_computed_across_the_dst_transition_uses_the_target_days_offset(self):
        """From the Friday BEFORE the last October Sunday, next Monday is on the other
        side of the CEST→CET switch: the Swedish 06–12 window must come back as 05–11
        UTC (winter), not the 04–10 of the summer clock the computation started in.
        Aware-datetime + timedelta is wall-clock arithmetic, so this holds — but nothing
        pinned it, and a 'fix' to instant arithmetic (a UTC add) would drift silently."""
        friday_before_switch = datetime(2026, 10, 23, 8, 0, 0)  # CEST; DST ends Oct 25
        assert friday_before_switch.weekday() == 4

        for _ in range(10):
            nxt = next_check_time([0], 72, friday_before_switch)
            local = stockholm(nxt)
            assert local.weekday() == 0
            assert local.date() == date(2026, 10, 26)  # the Monday after the switch
            assert 6 <= local.hour < 12  # Swedish förmiddag, judged on the winter clock
            assert 5 <= nxt.hour < 11  # = 05–11 stored UTC, not summer's 04–10

    def test_interval_snap_lands_in_the_swedish_window_in_winter(self):
        winter_tuesday = datetime(2026, 1, 20, 15, 30, 0)
        nxt = next_check_time([], 72, winter_tuesday)
        assert in_morning_window(nxt)


class TestWeeklySummarySlot:
    """The weekly summary is due Monday from the END of the förmiddag window, Swedish
    wall-clock — the first moment the week's Monday checks (ICA and Willys both) are in.
    The old rule was "Monday 14:00" applied on naive UTC: 15/16 Swedish depending on DST."""

    def test_due_from_noon_swedish_winter(self):
        # Winter (CET, UTC+1): 11:00 UTC == 12:00 local.
        monday = datetime(2026, 2, 16, 11, 0, 0)
        assert monday.weekday() == 0
        assert weekly_summary_slot(monday) == monday.date()

    def test_not_due_before_noon_swedish_winter(self):
        assert weekly_summary_slot(datetime(2026, 2, 16, 10, 59, 0)) is None

    def test_due_from_noon_swedish_summer(self):
        # Summer (CEST, UTC+2): 10:00 UTC == 12:00 local — an hour EARLIER in UTC.
        assert weekly_summary_slot(datetime(2026, 7, 27, 10, 0, 0)) == datetime(2026, 7, 27).date()

    def test_not_due_before_noon_swedish_summer(self):
        assert weekly_summary_slot(datetime(2026, 7, 27, 9, 59, 0)) is None

    def test_not_due_on_other_weekdays(self):
        tuesday = datetime(2026, 2, 17, 15, 0, 0)
        assert tuesday.weekday() == 1
        assert weekly_summary_slot(tuesday) is None

    def test_swedish_monday_night_is_not_utc_monday(self):
        """Monday 23:30 UTC is Tuesday 00:30 in Sweden — no longer Monday, no summary.
        The weekday is judged by the SWEDISH clock, same rule as the check window."""
        monday_2330_utc = datetime(2026, 2, 16, 23, 30, 0)
        assert monday_2330_utc.weekday() == 0  # UTC still says Monday...
        assert weekly_summary_slot(monday_2330_utc) is None  # ...Sweden says Tuesday

    def test_slot_is_the_local_monday_date(self):
        """Late Monday evening local: the slot is that Monday — the sender's dedup key
        must not flip at a UTC midnight that is 01:00/02:00 Swedish."""
        monday_evening = datetime(2026, 2, 16, 22, 0, 0)  # 23:00 local, still Monday
        assert weekly_summary_slot(monday_evening) == datetime(2026, 2, 16).date()
