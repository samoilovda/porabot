from datetime import datetime, timedelta

from sqlalchemy import text

from bot.database.engine import create_engine, dispose_engine, init_db


async def test_backfill_marks_legacy_overdue_pending_one_offs_as_already_delivered() -> None:
    """Regression: pre-existing overdue pending one-off reminders (from before
    last_fired_at existed) must not look "never delivered" after the column
    is added — reconcile_jobs_with_db would otherwise resend a notification
    the user already received, once for every such row, on first restart.

    The legacy rows are created against a hand-rolled pre-migration schema
    (no last_fired_at column at all) BEFORE init_db ever runs, so this
    matches the real deploy sequence: an old binary's data meets the new
    binary's very first startup. Backfills are guarded by schema_migrations
    to run exactly once ever (see bot/database/engine.py's _run_once), so
    calling init_db a second time on a DB it already migrated must NOT be
    the trigger for the backfill — only the first-ever run over this data is.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        past = datetime.utcnow() - timedelta(days=1)
        future = datetime.utcnow() + timedelta(days=1)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
                    "timezone VARCHAR DEFAULT 'UTC', language VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER, reminder_text VARCHAR, execution_time DATETIME, "
                    "status VARCHAR, is_recurring BOOLEAN, is_nagging BOOLEAN, "
                    "rrule_string VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(text("INSERT INTO users (id, timezone) VALUES (1, 'UTC')"))
            await conn.execute(
                text(
                    """
                    INSERT INTO reminders
                        (user_id, reminder_text, execution_time, status, is_recurring, is_nagging)
                    VALUES
                        (1, 'legacy overdue one-off', :past, 'pending', 0, 0),
                        (1, 'legacy future one-off', :future, 'pending', 0, 0),
                        (1, 'legacy overdue recurring', :past, 'pending', 1, 0)
                    """
                ),
                {"past": past, "future": future},
            )

        # First-ever init_db run on this pre-existing data: adds every
        # missing column (including last_fired_at) and runs the backfill.
        await init_db(engine)

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT reminder_text, last_fired_at FROM reminders ORDER BY id")
            )
            rows = {r[0]: r[1] for r in result.fetchall()}

        assert rows["legacy overdue one-off"] is not None
        assert rows["legacy future one-off"] is None
        assert rows["legacy overdue recurring"] is None

        # A later restart (schema_migrations already marks this backfill
        # done) must not re-touch rows a user has since interacted with —
        # e.g. resetting last_fired_at back would be wrong. Simulate a
        # legacy row inserted AFTER the migration already ran once: it must
        # be left alone, since the migration is "ever ran once", not
        # "runs until every row is covered".
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO reminders
                        (user_id, reminder_text, execution_time, status, is_recurring, is_nagging)
                    VALUES (1, 'post-migration overdue one-off', :past, 'pending', 0, 0)
                    """
                ),
                {"past": past},
            )

        await init_db(engine)

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT last_fired_at FROM reminders WHERE reminder_text = "
                    "'post-migration overdue one-off'"
                )
            )
            assert result.scalar_one() is None
    finally:
        await dispose_engine(engine)
