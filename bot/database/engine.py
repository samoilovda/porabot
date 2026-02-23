"""
Async SQLAlchemy engine and session factory.

Provides:
- Base — declarative base for all ORM models
- create_engine() — async engine factory
- create_session_maker() — session factory factory
- init_db() — creates all tables at startup
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine from a URL."""
    return create_async_engine(database_url, echo=False)


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables. Call once at startup."""
    from bot.database import models  # noqa: F401  (registers models in metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
