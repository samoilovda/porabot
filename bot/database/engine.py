"""
Async SQLAlchemy engine and session factory for Porabot.

URL formats:
  sqlite+aiosqlite:///porabot.db          — SQLite (default)
  postgresql+asyncpg://user:pass@host/db  — PostgreSQL

Session lifecycle: each handler/job opens its own session via `async with
session_pool()`. The DatabaseMiddleware commits on success and rolls back on
exception (Unit of Work). Background jobs manage their own sessions.
"""

from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = __import__("logging").getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Naive UTC now, matching the repo-wide invariant (see
    bot/database/models.py's module docstring) — datetime.utcnow() is
    deprecated since 3.12 and this is its direct replacement."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async engine from *database_url*. Call once at startup."""
    engine = create_async_engine(database_url, echo=False)

    if engine.dialect.name == "sqlite":
        # WAL mode lets a writer and readers proceed concurrently instead of
        # locking the whole file on every write; busy_timeout makes a writer
        # that does collide wait and retry instead of raising immediately.
        # Without this, the several per-minute cron jobs sharing this one
        # sqlite file (daily_briefs, habit_sweeper, missed_recovery,
        # habit_reports) intermittently fail a commit with "database is
        # locked" — for daily_briefs specifically, that meant a brief already
        # sent to the user but its "already sent today" flag never
        # persisted, so the same brief resent on the next tick.
        #
        # foreign_keys=ON: SQLite ships with FK enforcement OFF by default —
        # the models declare ForeignKey() but nothing was actually checking
        # them, so the same orphan-creating bug that raises IntegrityError on
        # PostgreSQL would silently corrupt data here. This only enforces
        # constraints on FUTURE writes on THIS connection (SQLite never
        # retroactively validates existing rows), so it can't crash on
        # startup over pre-existing data — see init_db's foreign_key_check
        # diagnostic below for surfacing those instead.
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to *engine*. Call once at startup."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _run_once(conn, name: str, run) -> None:
    """Run a one-time data migration identified by *name*, exactly once ever.

    Backs onto a plain `schema_migrations(name, applied_at)` table instead of
    Base.metadata so it stays independent of the ORM model set. Without this,
    a migration written as an unconditional UPDATE re-matches on every single
    startup — see the is_habit backfill this replaced, which kept
    reclassifying any plain daily+nagging reminder as a habit forever, not
    just the legacy rows it was meant to backfill once.
    """
    from sqlalchemy import text

    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR PRIMARY KEY, applied_at DATETIME)"
        )
    )
    already_applied = await conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE name = :name"), {"name": name}
    )
    if already_applied.first() is not None:
        return
    await run()
    await conn.execute(
        text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
        {"name": name, "now": _utcnow_naive()},
    )


async def _add_column_if_missing(conn, table: str, column) -> None:
    """ALTER TABLE ADD COLUMN *column* (a live SQLAlchemy Column from
    Base.metadata), but only if the table doesn't already have it.

    4.3: the type/NOT NULL/DEFAULT clause used to be a second copy of each
    column's definition, hand-typed as an ALTER-TABLE-shaped string
    alongside the Column already declared in models.py — the two had no
    way to stay in sync automatically, so a soft-migrated column changed
    in one place but not the other. CreateColumn(column).compile(...) asks
    SQLAlchemy to render the exact same DDL fragment it would use for this
    column inside CREATE TABLE, against the live connection's actual
    dialect — models.py stays the only place a column's definition lives.

    This used to just attempt the ALTER and swallow ANY
    OperationalError/ProgrammingError as "column already exists" — which
    also silently hides a permissions error, a locked/corrupt database, a
    typo'd column type, or any other genuine failure, on every single
    startup. Checking the inspector first means the ALTER is only even
    attempted when the column is actually missing, so an error from that
    point on is real and gets to propagate and fail startup loudly
    instead of being masked forever.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    existing_columns = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns(table)})
    if column.name in existing_columns:
        return
    column_ddl = str(CreateColumn(column).compile(dialect=conn.dialect))
    async with conn.begin_nested():
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_ddl}"))


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables defined in models.py. Call once at startup."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from bot.database import models  # noqa: F401 — registers models in metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Soft-migration: add any column models.py declares that an
        # existing table doesn't have yet (4.3). create_all above only
        # creates whole tables that don't exist at all — it never adds a
        # column to a table it finds already there — so this is what
        # actually carries an existing deployment's schema forward when a
        # model gains a new column. Iterates every table/column in
        # Base.metadata rather than hand-picking "the users columns" and
        # "the reminders columns": _add_column_if_missing already no-ops
        # for a column that exists, so this is exactly as safe for a
        # table that's never needed a soft migration (habit_events,
        # fsm_state) as for one that's had many.
        for table in Base.metadata.tables.values():
            for column in table.columns:
                await _add_column_if_missing(conn, table.name, column)

        # Backfill legacy habits created before `is_habit` existed.
        # Heuristic: daily recurring + nagging reminders were produced by Habits
        # flow. Guarded by _run_once (schema_migrations) — this heuristic also
        # matches a perfectly ordinary task the user turned into daily+nagging
        # through the edit keyboard, so it must run exactly once, not on every
        # startup, or every such task gets permanently reclassified as a habit.
        async def _backfill_is_habit() -> None:
            try:
                async with conn.begin_nested():
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
            except (OperationalError, ProgrammingError):
                # Extremely old schemas may temporarily miss one of these columns.
                pass

        await _run_once(conn, "backfill_is_habit_v1", _backfill_is_habit)

        # Backfill last_fired_at for pre-existing rows: it's used to tell
        # "never delivered" apart from "delivered, awaiting Done" for
        # overdue one-off reminders during reconcile_jobs_with_db. Without
        # this, every legacy overdue-but-still-pending one-off reminder
        # looks undelivered on the first post-deploy restart and gets a
        # duplicate catch-up notification, even if the user already saw it.
        # Guarded by _run_once too: its own WHERE (last_fired_at IS NULL) is
        # idempotent by accident (a fired reminder's last_fired_at is set at
        # fire time and never reset to NULL), but running it as unconditional
        # per-startup work is pure waste at scale — no reason to keep doing it
        # forever once every legacy row has been touched.
        async def _backfill_last_fired_at() -> None:
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            UPDATE reminders
                            SET last_fired_at = execution_time
                            WHERE last_fired_at IS NULL
                              AND status = 'pending'
                              AND COALESCE(is_recurring, 0) = 0
                              AND execution_time <= :now
                            """
                        ),
                        {"now": _utcnow_naive()},
                    )
            except (OperationalError, ProgrammingError):
                pass

        await _run_once(conn, "backfill_last_fired_at_v1", _backfill_last_fired_at)

        # PRAGMA foreign_keys=ON (see create_engine) only enforces
        # constraints on future writes — it never retroactively validates
        # rows that already exist. Surface any pre-existing orphan here as a
        # loud warning instead of leaving it to fail mysteriously the first
        # time something touches it, but don't block startup over it: fixing
        # historical data is a deliberate, separate migration, not something
        # to improvise at boot.
        if engine.dialect.name == "sqlite":
            violations = await conn.execute(text("PRAGMA foreign_key_check"))
            violation_rows = violations.fetchall()
            if violation_rows:
                logger.warning(
                    "Found %d foreign key violation(s) in existing data — "
                    "these rows predate strict FK enforcement and need a "
                    "dedicated cleanup migration: %s",
                    len(violation_rows),
                    violation_rows[:10],
                )


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine and close all connections. Call during shutdown."""
    if engine:
        logger.info("Disposing database engine.")
        await engine.dispose()
