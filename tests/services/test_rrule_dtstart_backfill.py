"""A-02: init_db's one-time backfill must seed rrule_dtstart = execution_time
for every pre-existing recurring reminder (a legacy row from before that
column existed), and must leave it alone for a non-recurring row — see
bot/database/engine.py's _backfill_rrule_dtstart.
"""

from datetime import datetime

from sqlalchemy import text

from bot.database.engine import create_engine, init_db


async def test_backfill_seeds_anchor_for_recurring_rows_only() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            # A legacy `reminders` table predating rrule_dtstart entirely.
            await conn.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
                    "timezone VARCHAR DEFAULT 'UTC', language VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(text("INSERT INTO users (id, timezone) VALUES (1, 'UTC')"))
            await conn.execute(
                text(
                    "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER, reminder_text VARCHAR, execution_time DATETIME, "
                    "status VARCHAR, is_recurring BOOLEAN, is_nagging BOOLEAN, "
                    "rrule_string VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO reminders (id, user_id, reminder_text, execution_time, status, "
                    "is_recurring, is_nagging, rrule_string) VALUES "
                    "(1, 1, 'daily standup', '2026-01-01 09:00:00', 'pending', 1, 0, 'FREQ=DAILY')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO reminders (id, user_id, reminder_text, execution_time, status, "
                    "is_recurring, is_nagging, rrule_string) VALUES "
                    "(2, 1, 'one-off errand', '2026-01-02 10:00:00', 'pending', 0, 0, NULL)"
                )
            )

        await init_db(engine)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id, execution_time, rrule_dtstart, is_recurring FROM reminders ORDER BY id")
            )
            rows = {r[0]: r for r in result.all()}

        recurring_row = rows[1]
        assert recurring_row[2] is not None
        assert str(recurring_row[2]).startswith("2026-01-01")

        one_off_row = rows[2]
        assert one_off_row[2] is None
    finally:
        from bot.database.engine import dispose_engine

        await dispose_engine(engine)


async def test_backfill_is_idempotent_and_does_not_reapply_on_second_init(monkeypatch) -> None:
    """_run_once must guard this like every other one-time migration —
    re-running init_db (e.g. every process restart) must not re-derive
    rrule_dtstart from execution_time forever, which would silently
    re-anchor a series the user has since snoozed away from its original
    creation-time execution_time."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)  # normal path: creates tables via Base.metadata, no legacy rows to backfill

        from bot.database.dao.reminder import ReminderDAO
        from bot.database.dao.user import UserDAO
        from bot.database.engine import create_session_maker

        session_pool = create_session_maker(engine)
        async with session_pool() as session:
            user = await UserDAO(session).get_or_create(1, timezone="UTC")
            reminder = await ReminderDAO(session).create_reminder(
                user_id=user.id,
                text="daily standup",
                execution_time=datetime(2026, 1, 1, 9, 0),
                is_recurring=True,
                rrule_string="FREQ=DAILY",
            )
            await session.commit()
            reminder_id = reminder.id

        # Simulate the anchor having since diverged from execution_time
        # (exactly what A-02 is meant to preserve across a restart).
        async with session_pool() as session:
            await session.execute(
                text("UPDATE reminders SET execution_time = '2026-03-01 09:00:00' WHERE id = :id"),
                {"id": reminder_id},
            )
            await session.commit()

        await init_db(engine)  # a second boot — must be a no-op for this row

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT rrule_dtstart FROM reminders WHERE id = :id"), {"id": reminder_id}
            )
            (rrule_dtstart,) = result.first()
        assert str(rrule_dtstart).startswith("2026-01-01")  # still the ORIGINAL anchor, not re-derived
    finally:
        from bot.database.engine import dispose_engine

        await dispose_engine(engine)
