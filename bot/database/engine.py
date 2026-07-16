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
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Soft-migration for Custom Daily Briefs Feature
        for col, col_type in [
            ("briefs_enabled", "BOOLEAN DEFAULT 1"),
            ("morning_brief_time", "VARCHAR DEFAULT '09:00'"),
            ("evening_brief_time", "VARCHAR DEFAULT '23:00'"),
            ("quiet_hours_enabled", "BOOLEAN DEFAULT 0"),
            ("quiet_hours_start", "VARCHAR DEFAULT '23:00'"),
            ("quiet_hours_end", "VARCHAR DEFAULT '07:00'"),
            ("missed_recovery_enabled", "BOOLEAN DEFAULT 1"),
            ("missed_recovery_time", "VARCHAR DEFAULT '10:00'"),
            ("last_missed_recovery_date", "VARCHAR"),
            ("habit_reports_enabled", "BOOLEAN DEFAULT 1"),
            ("habit_report_weekday", "INTEGER DEFAULT 6"),
            ("habit_report_time", "VARCHAR DEFAULT '23:50'"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_type}"))
            except OperationalError:
                pass  # column already exists

        # Soft-migration for nagging limits per reminder
        for col, col_type in [
            ("is_habit", "BOOLEAN NOT NULL DEFAULT 0"),
            ("is_fluid_habit", "BOOLEAN NOT NULL DEFAULT 0"),
            ("fluid_mode", "VARCHAR"),
            ("nagging_max_repeats", "INTEGER NOT NULL DEFAULT 3"),
            ("nagging_sent_count", "INTEGER NOT NULL DEFAULT 0"),
            ("habit_streak_current", "INTEGER NOT NULL DEFAULT 0"),
            ("habit_streak_best", "INTEGER NOT NULL DEFAULT 0"),
            ("habit_active_due_at", "DATETIME"),
            ("habit_last_completed_due_at", "DATETIME"),
            ("fluid_streak_current", "INTEGER NOT NULL DEFAULT 0"),
            ("fluid_streak_best", "INTEGER NOT NULL DEFAULT 0"),
            ("fluid_last_completed_date", "VARCHAR"),
            ("fluid_planned_date", "VARCHAR"),
            ("fluid_planned_time", "VARCHAR"),
            ("last_nag_chat_id", "BIGINT"),
            ("last_nag_message_id", "INTEGER"),
            ("completed_for_execution_time", "DATETIME"),
            ("last_completion_note", "VARCHAR"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE reminders ADD COLUMN {col} {col_type}"))
            except OperationalError:
                pass  # column already exists

        # Backfill legacy habits created before `is_habit` existed.
        # Heuristic: daily recurring + nagging reminders were produced by Habits flow.
        try:
            await conn.execute(
                text(
                    """
                    UPDATE reminders
                    SET is_habit = 1
                    WHERE COALESCE(is_habit, 0) = 0
                      AND COALESCE(is_recurring, 0) = 1
                      AND COALESCE(is_nagging, 0) = 1
                      AND UPPER(COALESCE(rrule_string, '')) LIKE 'FREQ=DAILY%'
                    """
                )
            )
        except OperationalError:
            # Extremely old schemas may temporarily miss one of these columns.
            pass


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine and close all connections. Call during shutdown."""
    if engine:
        logger.info("Disposing database engine.")
        await engine.dispose()
