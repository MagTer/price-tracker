"""Shared test fixtures — including the repo's first DB-backed tier.

Everything in `tests/` was an `AsyncMock` until Phase 04.1. That was tenable while the
schema was a byte-equivalent port of something already running; it stopped being tenable the
moment the schema itself became the deliverable. "Two links at one store persist", "a duplicate
store_url is rejected" and "the kr/unit ORDER BY sinks a NULL quantity to the bottom" are claims
about POSTGRES, and a mock cannot refute any of them.

`aiosqlite` cannot substitute: the models use `postgresql.UUID` and `JSONB`, which do not compile
on SQLite. So the integration tier talks to a real Postgres — and, because a laptop or a CI job
without one must still get a green `pytest` from the fast mocks-only suite, it SKIPS cleanly when
no database is reachable. That skip is what lets this tier exist at all; treat it as load-bearing.

The tier CREATE-DROPs its own database. It therefore forces the database NAME to a dedicated
throwaway (`price_tracker_test`), never reusing DATABASE_URL as-is — pointing a drop-and-recreate
fixture at the working dev database is the kind of mistake you only get to make once.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[1]

# The ONLY database this suite is ever allowed to create or drop.
TEST_DB_NAME = "price_tracker_test"

# The probe must fail fast, not hang: a developer with no Postgres should get a skip in seconds.
CONNECT_TIMEOUT_SECONDS = 3.0

# Truncated between tests so ordering cannot matter. `stores` is deliberately absent — the five
# stores are seeded BY the migration, and the tests use those seeds (which also proves the seed
# survived the 0001 rewrite).
_MUTABLE_TABLES = "price_points, watches, product_stores, products"

# Matches src/infra/db.py's local-dev default.
_LOCAL_DEV_URL = "postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker"


def _test_url() -> URL:
    """The throwaway test database's URL.

    Host/credentials come from TEST_DATABASE_URL, else DATABASE_URL, else the local-dev default.
    The database NAME is always overridden — that override is the guard in T-04.1-24, not a
    convenience.
    """
    raw = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or _LOCAL_DEV_URL
    return make_url(raw).set(database=TEST_DB_NAME)


def _asyncpg_dsn(url: URL) -> str:
    """SQLAlchemy URL -> a DSN asyncpg will accept (it does not know the +asyncpg suffix)."""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _recreate_test_database(url: URL) -> None:
    """Drop and recreate the throwaway database, from the server's maintenance database."""
    assert url.database == TEST_DB_NAME, (
        f"refusing to drop {url.database!r} — this fixture only ever touches {TEST_DB_NAME!r}"
    )
    admin_dsn = _asyncpg_dsn(url.set(database="postgres"))
    conn = await asyncpg.connect(admin_dsn, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        # WITH (FORCE) evicts a stale session left by an interrupted run (PG13+).
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


@contextmanager
def alembic_config(url: str) -> Iterator[Config]:
    """An Alembic Config bound to the test database.

    `alembic/env.py` PREFERS the DATABASE_URL environment variable over `sqlalchemy.url` (the
    deployment override), so setting the config option alone would silently migrate whatever
    DATABASE_URL happens to point at — the dev database, on most laptops. Both are set, and the
    environment variable is restored afterwards.
    """
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield cfg
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """A freshly created, empty `price_tracker_test` database — or a clean skip.

    Sync on purpose: `alembic.command` (used by `migrated_db`) calls `asyncio.run()` inside
    `env.py`, which cannot happen inside a running event loop.
    """
    url = _test_url()
    try:
        asyncio.run(_recreate_test_database(url))
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
        pytest.skip(
            f"No Postgres reachable at "
            f"{url.set(database='postgres').render_as_string(hide_password=True)}: "
            f"{type(exc).__name__}: {exc}. "
            f"Start one with `docker compose up -d postgres`, or set TEST_DATABASE_URL."
        )
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated_db(postgres_url: str) -> str:
    """`alembic upgrade head` applied to the fresh database. This IS MODEL-08's claim."""
    from alembic import command

    with alembic_config(postgres_url) as cfg:
        command.upgrade(cfg, "head")
    return postgres_url


@pytest.fixture(scope="session")
def alembic_env(migrated_db: str):
    """Factory yielding an Alembic Config bound to the migrated test DB (for `alembic check`)."""
    return lambda: alembic_config(migrated_db)


@pytest.fixture
async def db_engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    """A per-test engine against the migrated test database.

    Per-test, with NullPool: a session-scoped async engine would bind to the event loop of the
    first test that touched it, and pytest-asyncio gives each test its own loop.
    """
    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {_MUTABLE_TABLES} RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """The real thing the service layer takes — so a service can be driven against real SQL."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
