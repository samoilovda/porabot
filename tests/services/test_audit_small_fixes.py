"""B-17 (2026-11-04 audit): small fixes."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.lexicon import get_l10n
from bot.middlewares.notification_offer import NotificationOfferMiddleware

RU = get_l10n("ru")


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        s.add(User(id=1, username="u", timezone="UTC", language="ru"))
        await s.commit()
        yield s
    await engine.dispose()


async def test_offer_middleware_stops_querying_once_settled(session) -> None:
    dao = ReminderDAO(session)
    user = await session.get(User, 1)
    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    await dao.create_reminder(1, "a", later)
    await dao.create_reminder(1, "b", later)
    await session.commit()
    mw = NotificationOfferMiddleware()
    message = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(message=message)
    calls = []
    real_scalar = session.scalar

    async def counting_scalar(*a, **k):
        calls.append(1)
        return await real_scalar(*a, **k)

    session.scalar = counting_scalar
    data = {"reminder_dao": dao, "user": user, "l10n": RU}
    for _ in range(3):
        await mw(AsyncMock(return_value=None), event, data)
    assert len(calls) == 1  # past the first task: checked once, then skipped
    message.answer.assert_not_awaited()


async def test_offer_middleware_keeps_checking_a_user_with_no_tasks(session) -> None:
    dao = ReminderDAO(session)
    user = await session.get(User, 1)
    mw = NotificationOfferMiddleware()
    event = SimpleNamespace(message=SimpleNamespace(answer=AsyncMock()))
    await mw(AsyncMock(return_value=None), event, {"reminder_dao": dao, "user": user, "l10n": RU})
    assert 1 not in mw._settled


async def test_malformed_time_callback_gets_invalid_action() -> None:
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.handlers.reminders_wizard import callback_time_selected

    cb = SimpleNamespace(data="time_delta_abc", answer=AsyncMock(), message=SimpleNamespace())
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    user = SimpleNamespace(id=1, timezone="UTC")
    await callback_time_selected(cb, state, user, RU, None, None)
    cb.answer.assert_awaited_once_with(RU["invalid_action"], show_alert=True)


async def test_debug_summary_escapes_error_text(monkeypatch) -> None:
    from bot.config import config
    from bot.handlers.admin import cmd_debug

    monkeypatch.setattr(config, "ADMIN_ID", 5)

    async def boom(*a, **k):
        raise RuntimeError("no_such_table [x]")

    user_dao = SimpleNamespace(session=SimpleNamespace(execute=boom), count=AsyncMock(return_value=3))
    msg = SimpleNamespace(from_user=SimpleNamespace(id=5, full_name="a"), answer=AsyncMock())
    sched = SimpleNamespace(scheduler=SimpleNamespace(get_jobs=lambda: []))
    await cmd_debug(msg, user_dao, sched)
    text = msg.answer.await_args.args[0]
    assert "**" not in text
    assert "no\\_such\\_table \\[x]" in text


async def test_clear_all_unpins_the_brief(session) -> None:
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.database.dao.habit_event import HabitEventDAO
    from bot.handlers.settings.data import callback_clear_all_confirm

    user = await session.get(User, 1)
    user.pinned_brief_message_id = 77
    await session.commit()
    bot = SimpleNamespace(unpin_chat_message=AsyncMock())
    cb = SimpleNamespace(
        bot=bot, answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    )
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    sched = SimpleNamespace(remove_reminder_job=lambda _id: None)
    await callback_clear_all_confirm(cb, state, user, ReminderDAO(session), HabitEventDAO(session), sched, RU)
    bot.unpin_chat_message.assert_awaited_once_with(chat_id=1, message_id=77)
    assert user.pinned_brief_message_id is None


async def test_undo_from_list_sends_no_extra_message(session) -> None:
    from bot.database.dao.habit_event import HabitEventDAO
    from bot.handlers.reminders_completion import callback_done_undo

    dao = ReminderDAO(session)
    r = await dao.create_reminder(1, "x", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
    await dao.mark_done(r.id)
    await session.commit()
    msg = SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock())
    cb = SimpleNamespace(data=f"done_undo_{r.id}", message=msg, answer=AsyncMock())
    sched = SimpleNamespace(schedule_reminder=lambda *a, **k: None, remove_reminder_job=lambda *a: None)
    await callback_done_undo(cb, dao, HabitEventDAO(session), sched, await session.get(User, 1), RU, from_list=True)
    msg.answer.assert_not_awaited()
    msg.edit_reply_markup.assert_not_awaited()
    assert r.status == "pending"
