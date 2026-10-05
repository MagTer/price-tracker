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

    def test_install_captures_debug_fallback_records(self) -> None:
        """DEBUG is captured (several fallback events log at DEBUG) so the level filter works."""
        buf = get_log_buffer()
        buf.clear()
        install()
        try:
            logging.getLogger("domain.parser").debug("No usable JSON-LD Product, falling back")
            debug_msgs = [r["message"] for r in buf.get_records(min_level="DEBUG")]
            assert any("falling back" in m for m in debug_msgs)
            # ...but the default INFO view hides it.
            assert not any("falling back" in r["message"] for r in buf.get_records())
        finally:
            buf.clear()


class TestConsoleLoggingSurvivesTheRingBuffer:
    """Attaching the buffer must not silence stdout — v0.18.0 silently did exactly that.

    Nothing configures the root logger (uvicorn only configures its own), so root has no
    handlers and Python's `lastResort` printed WARNING+ to stderr. `callHandlers` skips
    lastResort as soon as it finds ANY handler up the chain — so the moment the ring buffer
    attached to `domain`/`infra`/`api`, every app record terminated in memory and reached
    stdout never. The Loggar page kept working, which is why seven days of prod logs held
    no app line at all.
    """

    def _clean_root(self):
        import logging

        root = logging.getLogger()
        saved = list(root.handlers), root.level
        root.handlers.clear()
        root.setLevel(logging.WARNING)
        return saved

    def _restore_root(self, saved):
        import logging

        handlers, level = saved
        root = logging.getLogger()
        root.handlers.clear()
        root.handlers.extend(handlers)
        root.setLevel(level)

    def test_install_gives_root_a_console_handler(self):
        import logging

        from infra.logbuffer import install

        saved = self._clean_root()
        try:
            install()
            root = logging.getLogger()
            assert any(isinstance(h, logging.StreamHandler) for h in root.handlers), (
                "install() attached the buffer without leaving anything that writes to stdout"
            )
            # INFO must survive root's default WARNING level, or "Checking price" never prints.
            assert root.level <= logging.INFO
        finally:
            self._restore_root(saved)

    def test_an_app_warning_reaches_the_console(self, capsys):
        import logging

        from infra.logbuffer import install

        saved = self._clean_root()
        try:
            install()
            logging.getLogger("infra.fetcher").warning("Bot wall from ica.se")
            err = capsys.readouterr().err
            assert "Bot wall from ica.se" in err, f"nothing reached stderr: {err!r}"
        finally:
            self._restore_root(saved)

    def test_ensure_console_logging_is_idempotent(self):
        import logging

        from infra.logbuffer import ensure_console_logging

        saved = self._clean_root()
        try:
            ensure_console_logging()
            ensure_console_logging()
            ensure_console_logging()
            root = logging.getLogger()
            streams = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
            # Three console handlers would print every line three times.
            assert len(streams) == 1
        finally:
            self._restore_root(saved)


class TestConsoleLinesAreJson:
    """Production's log store makes fields of JSON lines only; text lines read level "unknown"."""

    def _clean(self):
        root = logging.getLogger()
        saved = (
            list(root.handlers),
            root.level,
            {
                n: (list(logging.getLogger(n).handlers), logging.getLogger(n).propagate)
                for n in ("uvicorn", "uvicorn.access")
            },
        )
        root.handlers.clear()
        root.setLevel(logging.WARNING)
        return saved

    def _restore(self, saved):
        handlers, level, uv = saved
        root = logging.getLogger()
        root.handlers.clear()
        root.handlers.extend(handlers)
        root.setLevel(level)
        for name, (hs, prop) in uv.items():
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.handlers.extend(hs)
            lg.propagate = prop

    def _lines(self, err: str) -> list[dict]:
        import json

        return [json.loads(line) for line in err.splitlines() if line.strip()]

    def test_an_app_record_is_one_json_line_with_level_and_msg(self, capsys) -> None:
        from infra.logbuffer import ensure_console_logging

        saved = self._clean()
        try:
            ensure_console_logging()
            logging.getLogger("infra.fetcher").warning("Bot wall från %s", "ica.se")
            (line,) = self._lines(capsys.readouterr().err)
            assert line["level"] == "WARNING"
            assert line["msg"] == "Bot wall från ica.se"
            assert line["logger"] == "infra.fetcher"
            assert line["time"].endswith("Z")
        finally:
            self._restore(saved)

    def test_a_traceback_stays_inside_its_record(self, capsys) -> None:
        from infra.logbuffer import ensure_console_logging

        saved = self._clean()
        try:
            ensure_console_logging()
            try:
                raise ValueError("boom")
            except ValueError:
                logging.getLogger("domain.service").exception("check failed")
            (line,) = self._lines(capsys.readouterr().err)
            assert line["level"] == "ERROR"
            assert "ValueError: boom" in line["exc"]
        finally:
            self._restore(saved)

    def test_uvicorns_loggers_come_out_as_json_through_root(self, capsys) -> None:
        import logging.config

        from uvicorn.config import LOGGING_CONFIG

        from infra.logbuffer import ensure_console_logging

        saved = self._clean()
        try:
            # What uvicorn does before it imports the app: text handlers, no propagation.
            logging.config.dictConfig(LOGGING_CONFIG)
            ensure_console_logging()
            logging.getLogger("uvicorn.error").info("Application startup complete.")
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d', "10.0.0.1:5", "GET", "/health", "1.1", 200
            )
            lines = self._lines(capsys.readouterr().err)
            assert [(x["logger"], x["level"]) for x in lines] == [
                ("uvicorn.error", "INFO"),
                ("uvicorn.access", "INFO"),
            ]
            assert lines[1]["msg"] == '10.0.0.1:5 - "GET /health HTTP/1.1" 200'
        finally:
            self._restore(saved)
