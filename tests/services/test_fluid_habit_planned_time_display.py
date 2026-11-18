"""Audit 2026-11-18 #09: a fluid habit's planned hour for today must show in
the habit list and the task card (previously both always said "anytime today").
"Today" is the user's local date, not UTC.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytz
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, init_db
from bot.database.models import UiContext, User
from bot.handlers.experience import open_list, task_action
from bot.lexicon import get_l10n
from bot.utils.fluid_habit import fluid_planned_time_today, fluid_time_label

L10N = get_l10n("en")
TZ = "Pacific/Auckland"  # local date runs ahead of UTC for part of every day


def _today(tz=TZ) -> str:
    return datetime.now(pytz.timezone(tz)).date().isoformat()


def _yesterday(tz=TZ) -> str:
    return (datetime.now(pytz.timezone(tz)).date() - timedelta(days=1)).isoformat()


def test_helper_planned_today_in_user_timezone() -> None:
    habit = SimpleNamespace(fluid_planned_date=_today(), fluid_planned_time="18:30")
    assert fluid_planned_time_today(habit, TZ) == "18:30"
    assert fluid_time_label(habit, TZ, L10N) == L10N["habit_fluid_time_planned"].format(time="18:30")


def test_helper_yesterday_or_missing_means_anytime() -> None:
    old = SimpleNamespace(fluid_planned_date=_yesterday(), fluid_planned_time="18:30")
    none = SimpleNamespace(fluid_planned_date=None, fluid_planned_time=None)
    assert fluid_time_label(old, TZ, L10N) == L10N["habit_fluid_time_anytime"]
    assert fluid_time_label(none, TZ, L10N) == L10N["habit_fluid_time_anytime"]


def test_helper_uses_local_date_not_utc() -> None:
    # A date stored for the UTC "today" is NOT today for a user far ahead of
    # UTC whenever the two dates differ; the helper must follow the user's zone.
    utc_today = datetime.now(timezone.utc).date().isoformat()
    habit = SimpleNamespace(fluid_planned_date=utc_today, fluid_planned_time="09:00")
    local_today = _today()
    assert (fluid_planned_time_today(habit, TZ) == "09:00") == (utc_today == local_today)


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/fh.sqlite")
    await init_db(engine)
    maker = create_session_maker(engine)
    async with maker() as session:
        session.add(User(id=1, timezone=TZ, language="en"))
        await session.commit()
        yield session
    await engine.dispose()


def _msg():
    return SimpleNamespace(
        text="List", chat=SimpleNamespace(id=1), message_id=1,
        answer=AsyncMock(), edit_text=AsyncMock(), edit_reply_markup=AsyncMock(),
    )


async def _habit(dao, text, planned_date, planned_time):
    h = await dao.create_reminder(
        user_id=1, text=text, execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2),
        is_recurring=False, is_habit=True, is_fluid_habit=True,
    )
    h.fluid_planned_date, h.fluid_planned_time = planned_date, planned_time
    return h


async def test_list_and_card_show_planned_time(database) -> None:
    session = database
    user, dao = await session.get(User, 1), ReminderDAO(session)
    planned = await _habit(dao, "Run", _today(), "18:30")
    stale = await _habit(dao, "Read", _yesterday(), "07:00")
    await session.commit()

    msg = _msg()
    await open_list(msg, dao, user, L10N, kind="habits")
    text = msg.edit_text.await_args.args[0] if msg.edit_text.await_args else msg.answer.await_args.args[0]
    assert L10N["habit_fluid_time_planned"].format(time="18:30") in text
    assert "07:00" not in text
    assert L10N["habit_fluid_time_anytime"] in text  # the stale one

    ctx = (await session.scalars(select(UiContext).where(UiContext.kind == "list"))).first()
    for habit, expected in ((planned, "18:30"), (stale, None)):
        cb = SimpleNamespace(
            data=f"ux:{ctx.token}:0:detail:{habit.id}", answer=AsyncMock(), message=_msg(),
        )
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
        await task_action(cb, state, dao, HabitEventDAO(session), Mock(), user, L10N)
        card = cb.message.edit_text.await_args.args[0]
        if expected:
            assert L10N["habit_fluid_time_planned"].format(time=expected) in card
        else:
            assert L10N["habit_fluid_time_anytime"] in card
            assert "07:00" not in card
