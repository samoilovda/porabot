"""P1-2 regression: a deleted-but-not-yet-cleaned-up reminder must not be
resurrected by reconcile_jobs_with_db on the next process restart.

Before the fix, delete only removed the scheduler job and relied on an
in-memory asyncio.Task to hard-delete the DB row after the undo window — if
the process restarted during that window, the row was a completely normal
status='pending' reminder with no scheduler job, which is exactly the
signature reconcile_jobs_with_db uses to decide "this needs a fresh
catch-up job".
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders import callback_delete_task
from bot.lexicon import get_l10n
from bot.services.scheduler import SchedulerService

L10N = get_l10n("en")


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _callback(data: str):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=1,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


async def test_deleted_reminder_is_not_resurrected_by_reconcile_after_restart(session_factory) -> None:
    async with session_factory() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder = await ReminderDAO(session).create_reminder(
            user_id=1,
            text="Take a walk",
            execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )
        reminder_id = reminder.id
        await session.commit()

    # "Process A": the running bot. User taps delete; the job is removed and
    # pending_delete_at persisted. Simulate a restart happening immediately
    # after — inside the 5s undo window — by never running the cleanup sweep
    # and instead building a brand-new SchedulerService (fresh in-memory
    # scheduler, same DB) as "process B" would after a crash/redeploy.
    scheduler_a = AsyncIOScheduler()
    service_a = SchedulerService(scheduler_a, bot=SimpleNamespace(), session_pool=session_factory)
    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        user = await UserDAO(session).get_by_id(1)
        await callback_delete_task(
            _callback(f"del_task_{reminder_id}"),
            reminder_dao=reminder_dao,
            scheduler_service=service_a,
            user=user,
            l10n=L10N,
        )

    assert scheduler_a.get_job(str(reminder_id)) is None

    # "Process B" after restart: fresh scheduler, reconcile runs on startup.
    scheduler_b = AsyncIOScheduler()
    service_b = SchedulerService(scheduler_b, bot=SimpleNamespace(), session_pool=session_factory)

    await service_b.reconcile_jobs_with_db()

    # The whole point of persisting pending_delete_at: reconcile must see
    # this row as "being deleted", not "needs a catch-up job".
    assert scheduler_b.get_job(str(reminder_id)) is None
