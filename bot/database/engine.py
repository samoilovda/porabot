"""
Async SQLAlchemy engine and session factory for Porabot.

URL formats:
  sqlite+aiosqlite:///porabot.db          — SQLite (default)
  postgresql+asyncpg://user:pass@host/db  — PostgreSQL

Session lifecycle: each handler/job opens its own session via `async with
session_pool()`. The DatabaseMiddleware commits on success and rolls back on
exception (Unit of Work). Background jobs manage their own sessions.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = __import__("logging").getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async engine from *database_url*. Call once at startup."""
    return create_async_engine(database_url, echo=False)


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to *engine*. Call once at startup."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables defined in models.py. Call once at startup."""
    from bot.database import models  # noqa: F401 — registers models in metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine and close all connections. Call during shutdown."""
    if engine:
        logger.info("Disposing database engine.")
        await engine.dispose()