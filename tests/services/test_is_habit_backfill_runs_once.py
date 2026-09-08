from sqlalchemy import text

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, dispose_engine, init_db
from bot.database.models import User


async def test_is_habit_backfill_does_not_reclassify_plain_tasks_on_later_startups() -> None:
    """Regression (REWORK_PLAN_3 1.1): the is_habit backfill heuristic (daily
    recurring + nagging) also matches a perfectly ordinary task the user
    turned into daily+nagging through the edit keyboard AFTER the bot was
    already running the current binary. Without a "run once ever" guard,
    every startup (deploy, restart, crash) would permanently reclassify such
    a task as a habit — streaks, "Not today", missed-events, hard delete on
    removal — none of which the user ever opted into.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC"))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            reminder = await reminder_dao.create_reminder(
                user_id=1,
                text="water the plants",
                execution_time=__import__("datetime").datetime(2030, 1, 1, 9, 0),
                is_recurring=True,
                rrule_string="FREQ=DAILY",
                is_nagging=True,
            )
            reminder_id = reminder.id
            assert reminder.is_habit is False
            await session.commit()

        # Simulates a later restart (deploy, crash-recovery) — must not
        # re-run the legacy backfill over data created under the current binary.
        await init_db(engine)

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT is_habit FROM reminders WHERE id = :id"), {"id": reminder_id}
            )
            assert result.scalar_one() == 0
    finally:
        await dispose_engine(engine)
