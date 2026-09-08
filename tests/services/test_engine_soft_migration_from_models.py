"""Regression test for 4.3: init_db's soft-migration must add a column it
finds missing on an EXISTING table by reading the column's definition
straight out of Base.metadata (models.py), not from a hand-maintained
ALTER-TABLE-shaped string that used to live only in engine.py and could
drift out of sync with the model.

Simulates a legacy pre-migration `users` table — hand-rolled, missing
several columns models.py actually declares (mirrors
test_engine_last_fired_at_backfill.py's approach) — and asserts init_db
adds them all with the model's own type/nullability/default, and that a
pre-existing row picks up the right default value for a NOT NULL column.
"""

from sqlalchemy import inspect, text

from bot.database.engine import create_engine, dispose_engine, init_db


async def test_missing_model_columns_are_soft_added_with_correct_defaults() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            # A stripped-down legacy `users` table missing columns the
            # current User model declares (ics_feed_token, briefs_enabled,
            # habit_report_weekday, ...).
            await conn.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
                    "timezone VARCHAR DEFAULT 'UTC', language VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(
                text("INSERT INTO users (id, timezone) VALUES (1, 'UTC')")
            )
            await conn.execute(
                text(
                    "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER, reminder_text VARCHAR, execution_time DATETIME, "
                    "status VARCHAR, is_recurring BOOLEAN, is_nagging BOOLEAN, "
                    "rrule_string VARCHAR, created_at DATETIME)"
                )
            )

        await init_db(engine)

        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("users")}
            )
        # These exist in the current User model but not in the legacy
        # table above — init_db must have derived their DDL from
        # Base.metadata and added every one of them.
        for expected in (
            "ics_feed_token",
            "briefs_enabled",
            "habit_report_weekday",
            "quiet_hours_habits_exempt",
            "pinned_brief_message_id",
        ):
            assert expected in columns, f"{expected} was not soft-added"

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT briefs_enabled, habit_report_weekday FROM users WHERE id = 1")
            )
            briefs_enabled, habit_report_weekday = result.one()

        # NOT NULL soft-added columns must have picked up the model's own
        # server_default (True/6), not NULL, for the row that pre-dates
        # the column — SQLite requires a default to add such a column to
        # a non-empty table in the first place, and this is what proves
        # the compiled DDL actually carried it through.
        assert bool(briefs_enabled) is True
        assert habit_report_weekday == 6
    finally:
        await dispose_engine(engine)
