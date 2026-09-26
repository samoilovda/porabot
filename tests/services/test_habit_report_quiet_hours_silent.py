"""Step 5 integration check: a weekly habit report sent during the user's
quiet hours must actually carry disable_notification=True end to end
(_process_user_reports -> _send_safe -> bot.send_message), not just at the
notification_policy() unit level.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.habit_reports import _process_user_reports

DUE = datetime(2026, 9, 27, 9, 0, 0)


async def _seed_habit_with_a_weekly_event():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    session.add(
        User(
            id=1, username="u", timezone="UTC",
            quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="07:00",
        )
    )
    await session.flush()

    reminder_dao = ReminderDAO(session)
    habit = await reminder_dao.create_reminder(
        user_id=1, text="Workout", execution_time=DUE, is_recurring=True,
        rrule_string="FREQ=DAILY", is_habit=True, is_nagging=True,
    )
    habit.habit_active_due_at = DUE
    await session.flush()
    await HabitEventDAO(session).record(
        reminder=habit, user_tz="UTC", outcome="done", source="button", due_at_utc_naive=DUE
    )
    await session.commit()
    return session, engine


async def test_weekly_report_is_silent_inside_quiet_hours() -> None:
    session, engine = await _seed_habit_with_a_weekly_event()
    try:
        user = await session.get(User, 1)
        bot = AsyncMock()
        # Exactly 6 days after DUE, so week_start (today - 6 days) lands
        # precisely on DUE's date and the seeded event is inside the
        # weekly window. 23:30 is inside the user's 23:00-07:00 quiet window.
        report_date = (DUE + timedelta(days=6)).date()
        now_local = datetime(report_date.year, report_date.month, report_date.day, 23, 30)

        delivered = await _process_user_reports(session, bot, user, now_local)

        assert delivered is True
        bot.send_message.assert_awaited()
        for call in bot.send_message.await_args_list:
            assert call.kwargs["disable_notification"] is True
    finally:
        await engine.dispose()


async def test_weekly_report_is_loud_outside_quiet_hours() -> None:
    session, engine = await _seed_habit_with_a_weekly_event()
    try:
        user = await session.get(User, 1)
        bot = AsyncMock()
        report_date = (DUE + timedelta(days=6)).date()
        now_local = datetime(report_date.year, report_date.month, report_date.day, 12, 0)  # not quiet

        delivered = await _process_user_reports(session, bot, user, now_local)

        assert delivered is True
        bot.send_message.assert_awaited()
        for call in bot.send_message.await_args_list:
            assert call.kwargs["disable_notification"] is False
    finally:
        await engine.dispose()
