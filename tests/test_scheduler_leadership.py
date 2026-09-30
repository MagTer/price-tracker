"""One scheduler per database, and none at all where the installation says so.

Two different problems, two tools (v0.64.0):
* SCHEDULER_ENABLED switches an INSTALLATION off, for a second copy of the data (a lab)
  that would otherwise check every store beside production from the same public IP.
* The leader lock keeps two instances on ONE database from both working, whatever the
  replica count or a rollout's overlap.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import domain.scheduler as scheduler_module
from api.app import create_app
from domain.scheduler import PriceCheckScheduler, scheduler_enabled_from_env
from infra.leader_lock import PostgresLeaderLock


class FakeLock:
    def __init__(self, answers):
        self.answers = list(answers)
        self.released = 0

    async def acquire(self) -> bool:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def release(self) -> None:
        self.released += 1


def make(lock=None, enabled=True) -> PriceCheckScheduler:
    s = PriceCheckScheduler(
        session_factory=MagicMock(),
        fetcher=AsyncMock(),
        leader_lock=lock,
        enabled=enabled,
    )
    s._check_due_products = AsyncMock()
    s._check_summary_due = AsyncMock()
    return s


async def run_cycles(s: PriceCheckScheduler, monkeypatch, cycles: int) -> None:
    """Drive _run_loop for exactly `cycles` iterations, with the 5-minute sleep removed."""
    done = {"n": 0}

    async def fake_sleep(_seconds):
        done["n"] += 1
        if done["n"] >= cycles:
            s._running = False

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fake_sleep)
    s._running = True
    await s._run_loop()


class TestSwitch:
    @pytest.mark.parametrize("raw", [None, "", "  ", "true", "TRUE", "1", "yes", "on"])
    def test_on(self, raw):
        assert scheduler_enabled_from_env(raw) is True

    @pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off", " off "])
    def test_off(self, raw):
        assert scheduler_enabled_from_env(raw) is False

    @pytest.mark.parametrize("raw", ["flase", "disabled", "2", "nej"])
    def test_a_typo_is_refused_rather_than_guessed(self, raw):
        with pytest.raises(ValueError):
            scheduler_enabled_from_env(raw)

    def test_unset_environment_means_on(self, monkeypatch):
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
        assert scheduler_enabled_from_env() is True

    async def test_disabled_never_starts_and_says_so(self):
        lock = FakeLock([])
        s = make(lock, enabled=False)
        await s.start()
        assert s._task is None
        status = s.get_status()
        assert status["running"] is False
        assert status["enabled"] is False
        assert status["role"] == "disabled"


class TestLeadership:
    async def test_leader_does_the_work(self, monkeypatch):
        s = make(FakeLock([True, True]))
        await run_cycles(s, monkeypatch, 2)
        assert s._check_due_products.await_count == 2
        assert s._check_summary_due.await_count == 2
        assert s.get_status()["role"] == "leader"

    async def test_standby_does_nothing(self, monkeypatch):
        """Neither the checks nor the mail: both are what doubling would repeat."""
        s = make(FakeLock([False, False, False]))
        await run_cycles(s, monkeypatch, 3)
        s._check_due_products.assert_not_awaited()
        s._check_summary_due.assert_not_awaited()
        assert s.get_status()["role"] == "standby"

    async def test_standby_takes_over_when_the_lock_frees(self, monkeypatch):
        s = make(FakeLock([False, False, True]))
        await run_cycles(s, monkeypatch, 3)
        assert s._check_due_products.await_count == 1
        assert s.get_status()["role"] == "leader"

    async def test_an_unanswerable_lock_means_standby(self, monkeypatch):
        """Database down: working unguarded is the double-check the lock prevents."""
        s = make(FakeLock([ConnectionError("db down"), True]))
        await run_cycles(s, monkeypatch, 2)
        assert s._check_due_products.await_count == 1

    async def test_no_lock_means_always_leader(self, monkeypatch):
        s = make(None)
        await run_cycles(s, monkeypatch, 1)
        assert s._check_due_products.await_count == 1

    async def test_stop_releases_the_lock(self):
        lock = FakeLock([True] * 5)
        s = make(lock)
        s.CHECK_INTERVAL_SECONDS = 3600
        await s.start()
        await asyncio.sleep(0)
        await s.stop()
        assert lock.released == 1


class TestHealth:
    def test_health_names_a_disabled_scheduler(self, monkeypatch):
        from infra import db

        app = create_app()
        app.state.scheduler = make(FakeLock([]), enabled=False)

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def execute(self, *_a, **_k):
                return None

        monkeypatch.setattr("api.app.engine", MagicMock(connect=lambda: Conn()))
        assert db  # the real engine module is imported but not used
        body = TestClient(app).get("/health").json()
        assert body["scheduler_running"] is False
        assert body["scheduler_enabled"] is False
        assert body["scheduler_role"] == "disabled"


@pytest.mark.integration
class TestPostgresLeaderLock:
    """Two instances against one real database, as two replicas would be."""

    async def test_one_holder_at_a_time_and_takeover_when_the_holder_dies(self, postgres_url):
        e1 = create_async_engine(postgres_url)
        e2 = create_async_engine(postgres_url)
        e3 = create_async_engine(postgres_url)
        a, b = PostgresLeaderLock(e1), PostgresLeaderLock(e2)
        try:
            assert await a.acquire() is True
            assert await b.acquire() is False
            assert await a.acquire() is True  # still held, same connection

            # A clean handover: release, and the standby wins its next ask.
            await a.release()
            assert await b.acquire() is True
            assert await a.acquire() is False

            # The holder's connection dies (a crashed pod): Postgres frees the lock.
            async with e3.connect() as admin:
                pid = (
                    await admin.execute(
                        text(
                            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' "
                            "AND objid = (:k & 4294967295) AND granted"
                        ),
                        {"k": 0x70726963657363},
                    )
                ).scalar_one()
                await admin.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            assert await a.acquire() is True
            # The dead holder notices on its next ask and competes again, and loses.
            assert await b.acquire() is False

            # The held connection is not left idle in a transaction.
            async with e3.connect() as admin:
                states = (
                    await admin.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE state = 'idle in transaction' AND datname = current_database()"
                        )
                    )
                ).scalar_one()
            assert states == 0
        finally:
            await a.release()
            await b.release()
            for e in (e1, e2, e3):
                await e.dispose()
