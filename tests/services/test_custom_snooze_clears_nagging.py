"""Audit 2026-11-18 #04: snoozing via "Свой вариант" (custom time) used to
leave the nag_<id> follow-up job and last_nag_* message pointers behind, so
the repeated-signal chain kept firing after the user had postponed the task.
The quick-snooze path already removed it; both paths must behave the same.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders_shared import _save_and_show_edit
from bot.handlers.reminders_snooze import callback_snooze_act
from bot.lexicon import get_l10n


async def _seeded_dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.commit()
    return ReminderDAO(session), engine


def _scheduler(nag_removed: list[int]):
    return SimpleNamespace(
        schedule_reminder=lambda *a, **k: None,
        schedule_snooze=lambda *a, **k: None,
        remove_snooze_job=lambda rid: None,
        remove_nagging_job=lambda rid: nag_removed.append(rid),
        remove_reminder_job=lambda rid: None,
    )


async def _nagging_reminder(dao: ReminderDAO):
    r = await dao.create_reminder(
        user_id=1, text="pay rent",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
    )
    r.is_nagging = True
    r.last_nag_chat_id = 1
    r.last_nag_message_id = 42
    await dao.session.commit()
    return r


async def test_custom_snooze_time_input_removes_nag_job_and_pointers() -> None:
    dao, engine = await _seeded_dao()
    try:
        r = await _nagging_reminder(dao)
        state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        await state.update_data(
            text="pay rent", edit_reminder_id=r.id, is_snooze_mode=True,
            execution_time=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        )
        nag_removed: list[int] = []
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        msg = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())

        await _save_and_show_edit(msg, state, get_l10n("en"), user, dao, _scheduler(nag_removed))

        assert nag_removed == [r.id]
        assert r.last_nag_chat_id is None
        assert r.last_nag_message_id is None
        assert r.snooze_count == 1
    finally:
        await engine.dispose()


async def test_plain_edit_does_not_touch_nag_job() -> None:
    dao, engine = await _seeded_dao()
    try:
        r = await _nagging_reminder(dao)
        state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        await state.update_data(
            text="pay rent", edit_reminder_id=r.id,
            execution_time=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        )
        nag_removed: list[int] = []
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        msg = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())

        await _save_and_show_edit(msg, state, get_l10n("en"), user, dao, _scheduler(nag_removed))

        assert nag_removed == []
        assert r.last_nag_message_id == 42
    finally:
        await engine.dispose()


async def test_quick_snooze_still_removes_nag_job() -> None:
    dao, engine = await _seeded_dao()
    try:
        r = await _nagging_reminder(dao)
        nag_removed: list[int] = []
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        callback = SimpleNamespace(
            data=f"snooze_act_{r.id}_1h",
            message=SimpleNamespace(text="pay rent", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
            answer=AsyncMock(),
        )
        await callback_snooze_act(callback, dao, _scheduler(nag_removed), SimpleNamespace(), user, get_l10n("en"))

        assert nag_removed == [r.id]
        assert r.last_nag_message_id is None
    finally:
        await engine.dispose()


async def test_custom_option_passes_utc_offset_flag_to_keyboard(monkeypatch) -> None:
    import bot.handlers.reminders_snooze as mod

    seen: dict = {}

    def fake_kb(tz, l10n, show_utc_offset=False, **kw):
        seen["show_utc_offset"] = show_utc_offset
        return None

    monkeypatch.setattr(mod, "get_time_selection_keyboard", fake_kb)
    reminder = SimpleNamespace(id=5, reminder_text="x", tags=None, priority=None)
    dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder))
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=True)
    callback = SimpleNamespace(
        data="snooze_act_5_custom",
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await mod.callback_snooze_act(callback, dao, SimpleNamespace(), state, user, get_l10n("en"))

    assert seen["show_utc_offset"] is True
