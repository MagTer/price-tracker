import os
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
