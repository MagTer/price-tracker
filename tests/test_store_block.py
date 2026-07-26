"""Tests for StoreBlockRegistry — THE shared per-store circuit breaker.

The breaker used to be a private dict inside PriceCheckScheduler, so only background checks
stood down after a bot wall while every interactive path kept hitting the store. These tests
pin the two properties that fixed it: the state is shared, and each consecutive block waits
longer than the last.
"""

from datetime import UTC, datetime, timedelta

from infra.store_block import BASE_COOLDOWN_MINUTES, MAX_COOLDOWN_MINUTES, StoreBlockRegistry


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TestShippedDefaults:
    """The defaults are the only values prod actually runs — every other test here passes
    base_minutes explicitly, so nothing else would notice them changing."""

    def test_base_cooldown_is_five_minutes(self) -> None:
        """Measured, not guessed: a 2026-07-26 ICA wall had lifted 17 minutes later for the
        identical client from the identical IP, and the requests around it were 2 minutes
        apart. A sampled challenge does not deserve a half-hour stand-down — see the constant's
        comment. Raising this back to 30 costs half an hour of coverage per unlucky request."""
        assert BASE_COOLDOWN_MINUTES == 5

    def test_the_escalation_ceiling_still_covers_a_real_flag(self) -> None:
        """Shortening the base only works because the doubling is untouched: a store that
        genuinely flags us still reaches hours (5 → 10 → 20 → 40 → 80 → 160 → 240)."""
        assert MAX_COOLDOWN_MINUTES == 240

        registry = StoreBlockRegistry()
        waits = []
        for _ in range(7):
            until = registry.record_block("ica")
            waits.append(round((until - _now()).total_seconds() / 60))
        assert waits == [5, 10, 20, 40, 80, 160, 240]


class TestStoreBlockRegistry:
    def test_a_fresh_store_is_not_blocked(self) -> None:
        registry = StoreBlockRegistry()
        assert registry.blocked_until("ica") is None
        assert registry.is_blocked("ica") is False

    def test_record_block_opens_the_breaker_for_the_base_window(self) -> None:
        registry = StoreBlockRegistry(base_minutes=30, max_minutes=240)
        until = registry.record_block("ica", store_name="ICA", source="scheduler")

        assert registry.is_blocked("ica") is True
        # ~30 minutes out, allowing for the clock ticking during the call.
        assert timedelta(minutes=29) < (until - _now()) <= timedelta(minutes=30)

    def test_consecutive_blocks_double_the_cooldown(self) -> None:
        """A flat cooldown re-probes a walled store at the same cadence forever."""
        registry = StoreBlockRegistry(base_minutes=30, max_minutes=240)
        waits = []
        for _ in range(4):
            before = _now()
            until = registry.record_block("ica", store_name="ICA", source="scheduler")
            waits.append(round((until - before).total_seconds() / 60))
        assert waits == [30, 60, 120, 240]

    def test_the_cooldown_is_capped(self) -> None:
        registry = StoreBlockRegistry(base_minutes=30, max_minutes=90)
        for _ in range(10):
            until = registry.record_block("ica")
        assert (until - _now()) <= timedelta(minutes=90)

    def test_success_clears_the_breaker_and_the_escalation(self) -> None:
        registry = StoreBlockRegistry(base_minutes=30, max_minutes=240)
        registry.record_block("ica")
        registry.record_block("ica")  # now at 60 min
        registry.record_success("ica", store_name="ICA")

        assert registry.blocked_until("ica") is None
        before = _now()
        until = registry.record_block("ica")
        # Back to the BASE window: healthy traffic must not ratchet the count upward.
        assert round((until - before).total_seconds() / 60) == 30

    def test_an_elapsed_cooldown_lifts_but_keeps_the_strike_count(self) -> None:
        """The probe after a lapse is on trial — a second wall escalates, not restarts."""
        registry = StoreBlockRegistry(base_minutes=30, max_minutes=240)
        registry.record_block("ica")
        registry._until["ica"] = _now() - timedelta(seconds=1)  # already elapsed

        assert registry.blocked_until("ica") is None

        before = _now()
        until = registry.record_block("ica")
        assert round((until - before).total_seconds() / 60) == 60

    def test_blocks_are_per_store(self) -> None:
        registry = StoreBlockRegistry()
        registry.record_block("ica", store_name="ICA")
        assert registry.is_blocked("ica") is True
        assert registry.is_blocked("willys") is False

    def test_success_on_an_unknown_store_is_a_no_op(self) -> None:
        registry = StoreBlockRegistry()
        registry.record_success("never-seen")  # must not raise or create state
        assert registry.snapshot() == []

    def test_snapshot_lists_only_currently_blocked_stores(self) -> None:
        registry = StoreBlockRegistry(base_minutes=30)
        registry.record_block("ica-id", store_name="ICA")
        registry.record_block("ica-id", store_name="ICA")
        registry.record_block("willys-id", store_name="Willys")
        registry.record_success("willys-id")

        snapshot = registry.snapshot()
        assert [row["store"] for row in snapshot] == ["ICA"]
        assert snapshot[0]["consecutive_blocks"] == 2
