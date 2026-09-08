"""4.3: end-to-end wiring — #tag/!priority parsed out of a task phrase are
carried through the FSM and persisted on the created Reminder."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.reminders import _handle_parsed_result
from bot.lexicon.ru import RU


def _make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


async def _seeded_dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    return ReminderDAO(session), engine


async def test_tags_and_priority_survive_parse_to_save() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        result = SimpleNamespace(
            clean_text="купить молоко #дом !1",
            parsed_datetime=future_dt,
            confidence=1.0,
        )
        source_message = SimpleNamespace(
            chat=SimpleNamespace(id=1),
            answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1)),
        )
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        # confidence=1.0 clears the confirmation screen — _handle_parsed_result
        # saves the reminder directly via _save_and_show_edit internally, so
        # by the time it returns the FSM data has already been cleared.
        await _handle_parsed_result(source_message, state, user, RU, result, reminder_dao, scheduler_service)

        reminders = await reminder_dao.get_user_reminders(1)
        assert len(reminders) == 1
        assert reminders[0].reminder_text == "купить молоко"
        assert reminders[0].tags == "дом"
        assert reminders[0].priority == 1
    finally:
        await engine.dispose()


async def test_no_tags_or_priority_leaves_columns_null() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        result = SimpleNamespace(clean_text="plain task", parsed_datetime=future_dt, confidence=1.0)
        source_message = SimpleNamespace(
            chat=SimpleNamespace(id=1),
            answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1)),
        )
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _handle_parsed_result(source_message, state, user, RU, result, reminder_dao, scheduler_service)

        reminders = await reminder_dao.get_user_reminders(1)
        assert reminders[0].tags is None
        assert reminders[0].priority is None
    finally:
        await engine.dispose()
