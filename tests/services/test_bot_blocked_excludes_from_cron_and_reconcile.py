"""2.7: a user who has blocked the bot (User.bot_blocked_at set) must be
excluded from every cron job's candidate query that would otherwise try
to SEND them a Telegram message — daily briefs, missed-task recovery,
habit reports — and from SchedulerService.reconcile_jobs_with_db, which
would otherwise keep recreating a job for their reminders that can only
ever fail with TelegramForbiddenError.

habit_sweeper.py is deliberately NOT included here — it never sends a
message, only records habit_events for streak/report accuracy, and a
blocked user can still unblock or check progress via the Mini App; see
its own module comment for why excluding it would be a data-correctness
regression, not a performance win.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, dispose_engine, init_db
from bot.database.models import User
from bot.services.daily_briefs import get_users_needing_brief_check
from bot.services.scheduler import SchedulerService


async def test_blocked_user_excluded_from_daily_briefs_candidates() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC", briefs_enabled=True))
            session.add(
                User(
                    id=2, timezone="UTC", briefs_enabled=True,
                    bot_blocked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            await session.flush()
            reminder_dao = ReminderDAO(session)
            future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            await reminder_dao.create_reminder(user_id=1, text="task", execution_time=future, is_recurring=False)
            await reminder_dao.create_reminder(user_id=2, text="task", execution_time=future, is_recurring=False)
            await session.commit()

        async with session_pool() as session:
            user_ids = await get_users_needing_brief_check(session)

        assert user_ids == [1]
    finally:
        await dispose_engine(engine)


async def test_blocked_users_reminder_is_not_reconciled() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC"))
            session.add(
                User(id=2, timezone="UTC", bot_blocked_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
            await session.flush()
            reminder_dao = ReminderDAO(session)
            future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            r1 = await reminder_dao.create_reminder(user_id=1, text="ok", execution_time=future, is_recurring=False)
            r2 = await reminder_dao.create_reminder(
                user_id=2, text="blocked", execution_time=future, is_recurring=False
            )
            await session.commit()

        scheduler = AsyncIOScheduler()
        service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=session_pool)
        await service.reconcile_jobs_with_db()

        assert scheduler.get_job(str(r1.id)) is not None
        assert scheduler.get_job(str(r2.id)) is None
    finally:
        await dispose_engine(engine)


async def test_forbidden_send_sets_bot_blocked_at_as_a_safety_net() -> None:
    """Even without a my_chat_member update ever arriving (Telegram doesn't
    guarantee its delivery the way it does ordinary messages), a confirmed
    TelegramForbiddenError from an actual send attempt is unambiguous
    proof — SchedulerService._execute_reminder must mark it immediately."""
    from aiogram.exceptions import TelegramForbiddenError

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC"))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            reminder = await reminder_dao.create_reminder(
                user_id=1, text="task", execution_time=past, is_recurring=False
            )
            await session.commit()
            reminder_id = reminder.id

        scheduler = AsyncIOScheduler()
        fake_bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden")
            )
        )
        service = SchedulerService(scheduler, bot=fake_bot, session_pool=session_pool)

        await service._execute_reminder(reminder_id)

        async with session_pool() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.id == 1))
            user = result.scalar_one()
            assert user.bot_blocked_at is not None
    finally:
        await dispose_engine(engine)
