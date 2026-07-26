"""In-memory ring buffer of recent log records, surfaced in the portal's Loggar page.

The app already logs the interesting operational story — which extraction path won
(API / JSON-LD / LLM), when a model fell back to the next in the cascade, when a fetch
hit a store's WAF wall, when metadata extraction gave up. Rather than thread a second
event bus through all of that, this handler tails those existing log records into a
capped deque the admin API can read. Ephemeral by design: it lives in the process, so a
restart clears it — fine for an at-a-glance operator view on a single-instance app.

Attached to the `domain`, `infra`, and `api` loggers (not root), so uvicorn's per-request
access spam stays out and only the app's own business events land here.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from threading import Lock

# The attribute names a bare LogRecord already carries; anything else on a record is an
# `extra=` field a call site attached (store, product, confidence …) and worth surfacing.
_STANDARD_RECORD_KEYS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

_DEFAULT_LOGGERS = ("domain", "infra", "api")


class RingBufferLogHandler(logging.Handler):
    """Keeps the last `capacity` log records as plain dicts, newest appended last."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self._records: deque[dict[str, object]] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            extras = {
                k: v
                for k, v in record.__dict__.items()
                if k not in _STANDARD_RECORD_KEYS and not k.startswith("_")
            }
            if extras:
                message += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
            entry = {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "levelno": record.levelno,
                "logger": record.name,
                "message": message,
            }
        except Exception:  # a logging handler must never raise into the caller
            return
        with self._lock:
            self._records.append(entry)

    def get_records(self, *, limit: int = 200, min_level: str = "INFO") -> list[dict[str, object]]:
        """Newest-first records at or above `min_level`, capped at `limit`."""
        threshold = logging.getLevelName(min_level.upper())
        if not isinstance(threshold, int):
            threshold = logging.INFO
        with self._lock:
            items = list(self._records)
        filtered = [r for r in items if int(r["levelno"]) >= threshold]  # type: ignore[arg-type]
        filtered.reverse()
        return filtered[: max(1, limit)]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_buffer: RingBufferLogHandler | None = None


def get_log_buffer() -> RingBufferLogHandler:
    global _buffer
    if _buffer is None:
        _buffer = RingBufferLogHandler()
    return _buffer


def ensure_console_logging(level: int = logging.INFO) -> None:
    """Give the ROOT logger a stderr handler if nothing else has. Idempotent.

    This is not decoration — without it the app is silent in `docker logs`, and it is
    `install()` below that makes it so. The mechanism is a genuine Python trap:

    Nothing in this app or in uvicorn configures the root logger (uvicorn only configures its
    own `uvicorn*` loggers), so root ends up with NO handlers. Python covers that case with
    `logging.lastResort`, which prints WARNING+ to stderr — but `Logger.callHandlers` only
    falls back to `lastResort` when it found **zero** handlers anywhere up the chain. The
    moment `install()` attaches the ring buffer to `domain`/`infra`/`api`, a handler IS found,
    `lastResort` is skipped, and every app record now terminates in the in-memory buffer and
    reaches stdout never. The portal's Loggar page kept working, which is precisely why this
    went unnoticed: v0.18.0 traded `docker logs` for the Loggar page without anyone choosing
    to. Seven days of prod logs contained no app line at all — only uvicorn access logs.

    Attaching to root (not to the app loggers) keeps one console handler for the whole
    process instead of one per logger, so nothing is printed three times.
    """
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(handler)
    # Root defaults to WARNING; INFO records would be dropped before reaching the handler.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)


def install(level: int = logging.DEBUG, loggers: tuple[str, ...] = _DEFAULT_LOGGERS) -> None:
    """Attach the ring buffer to the app's loggers. Idempotent.

    Also raises each named logger to `level` so its records are actually emitted regardless
    of how the root logger is configured, without touching the root logger's level or its
    other handlers. Capturing at DEBUG matters: several *fallback* events (JSON-LD → LLM,
    "confidence too low, trying next model") are DEBUG, and the Loggar page's level filter
    can only show what the buffer holds. These DEBUG records stay OUT of the console — they
    propagate to the root handler, which filters at its own (INFO+) level; only this
    buffer, attached directly to the app loggers, keeps them.

    Calls ensure_console_logging() FIRST: attaching this handler is what suppresses Python's
    lastResort fallback, so the console handler must exist before that happens. See there.
    """
    ensure_console_logging()
    handler = get_log_buffer()
    handler.setLevel(level)
    for name in loggers:
        lg = logging.getLogger(name)
        if handler not in lg.handlers:
            lg.addHandler(handler)
        if lg.level == logging.NOTSET or lg.level > level:
            lg.setLevel(level)
