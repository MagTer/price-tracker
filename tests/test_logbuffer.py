"""Tests for the in-memory log ring buffer behind the portal's Loggar page."""

import logging

from infra.logbuffer import RingBufferLogHandler, get_log_buffer, install


def _record(name: str, level: int, msg: str, **extra) -> logging.LogRecord:
    rec = logging.LogRecord(name, level, __file__, 0, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


class TestRingBuffer:
    def test_captures_message_newest_first(self) -> None:
        h = RingBufferLogHandler()
        h.emit(_record("domain.parser", logging.INFO, "first"))
        h.emit(_record("domain.parser", logging.INFO, "second"))

        recs = h.get_records()
        assert [r["message"] for r in recs] == ["second", "first"]
        assert recs[0]["level"] == "INFO"
        assert recs[0]["logger"] == "domain.parser"
        assert "ts" in recs[0]

    def test_extra_fields_are_appended_to_message(self) -> None:
        h = RingBufferLogHandler()
        h.emit(_record("domain.parser", logging.WARNING, "extraction failed", store="ica"))

        assert "store=ica" in h.get_records()[0]["message"]

    def test_percent_style_args_are_rendered(self) -> None:
        h = RingBufferLogHandler()
        rec = logging.LogRecord(
            "domain", logging.INFO, __file__, 0, "Checking %d products", (5,), None
        )
        h.emit(rec)

        assert h.get_records()[0]["message"] == "Checking 5 products"

    def test_min_level_filters_below_threshold(self) -> None:
        h = RingBufferLogHandler()
        h.emit(_record("infra.fetcher", logging.INFO, "info line"))
        h.emit(_record("infra.fetcher", logging.WARNING, "warn line"))
        h.emit(_record("infra.fetcher", logging.ERROR, "error line"))

        msgs = [r["message"] for r in h.get_records(min_level="WARNING")]
        assert msgs == ["error line", "warn line"]
        assert [r["message"] for r in h.get_records(min_level="ERROR")] == ["error line"]

    def test_unknown_level_defaults_to_info(self) -> None:
        h = RingBufferLogHandler()
        h.emit(_record("api.admin", logging.DEBUG, "debug line"))
        h.emit(_record("api.admin", logging.INFO, "info line"))

        # Garbage level string falls back to INFO, so the DEBUG line is excluded.
        assert [r["message"] for r in h.get_records(min_level="bogus")] == ["info line"]

    def test_limit_caps_and_capacity_evicts_oldest(self) -> None:
        h = RingBufferLogHandler(capacity=3)
        for i in range(5):
            h.emit(_record("domain", logging.INFO, f"m{i}"))

        # Only the last 3 survive the capped deque, newest first.
        assert [r["message"] for r in h.get_records()] == ["m4", "m3", "m2"]
        assert [r["message"] for r in h.get_records(limit=1)] == ["m4"]

    def test_emit_never_raises(self) -> None:
        h = RingBufferLogHandler()
        broken = logging.LogRecord("domain", logging.INFO, __file__, 0, "%d", ("not-an-int",), None)
        h.emit(broken)  # getMessage() would raise on the bad %d — must be swallowed
        # Nothing recorded, but no exception propagated.
        assert h.get_records() == []


class TestInstall:
    def test_install_attaches_and_captures_from_app_loggers(self) -> None:
        buf = get_log_buffer()
        buf.clear()
        install()
        try:
            logging.getLogger("domain.parser").info("hello from parser")
            messages = [r["message"] for r in buf.get_records()]
            assert any("hello from parser" in m for m in messages)
        finally:
            buf.clear()

    def test_install_is_idempotent(self) -> None:
        install()
        install()
        lg = logging.getLogger("infra")
        assert lg.handlers.count(get_log_buffer()) == 1
