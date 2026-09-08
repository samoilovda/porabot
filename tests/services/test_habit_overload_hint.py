"""3.6: soft, one-time hint on creating the 11th active habit — not a hard
limit, just a nudge shown exactly once as the threshold is crossed."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.habits import state_habit_time
from bot.lexicon.ru import RU
from bot.services.scheduler import SchedulerService
from apscheduler.schedulers.asyncio import AsyncIOScheduler


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class FakeFSM:
    def __init__(self, data):
        self._data = data

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self._data = {}


async def _create_n_habits(reminder_dao: ReminderDAO, n: int) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(n):
        await reminder_dao.create_reminder(
            user_id=1,
            text=f"Habit {i}",
            execution_time=now + timedelta(hours=1),
            is_recurring=True,
            rrule_string="FREQ=DAILY",
            is_habit=True,
            is_nagging=True,
        )


async def _run_state_habit_time(session, existing_habits: int) -> str:
    user = User(id=1, username="u", timezone="UTC")
    session.add(user)
    await session.flush()
    reminder_dao = ReminderDAO(session)
    await _create_n_habits(reminder_dao, existing_habits)

    scheduler = AsyncIOScheduler()
    scheduler_service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)

    state = FakeFSM({"habit_text": "New habit"})
    message = SimpleNamespace(text="10:00", answer=AsyncMock())

    await state_habit_time(message, state, user, reminder_dao, scheduler_service, RU)

    return message.answer.await_args.args[0]


async def test_eleventh_habit_shows_overload_hint(session) -> None:
    # 10 existing + this one being created = 11th.
    text = await _run_state_habit_time(session, existing_habits=10)
    assert RU["habit_overload_hint"] in text


async def test_tenth_habit_does_not_show_hint(session) -> None:
    text = await _run_state_habit_time(session, existing_habits=9)
    assert RU["habit_overload_hint"] not in text


async def test_twelfth_habit_does_not_repeat_hint(session) -> None:
    # One-time: only the 11th shows it, not every habit after.
    text = await _run_state_habit_time(session, existing_habits=11)
    assert RU["habit_overload_hint"] not in text
