"""Audit 2026-11-18 #02 (T-02, UI): when a recurring reminder is snoozed past
its next regular occurrence, the confirmation also names that occurrence.
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
from bot.handlers.reminders_shared import _save_and_show_edit, series_next_line
from bot.handlers.reminders_snooze import callback_snooze_act
from bot.lexicon import get_l10n

L10N = get_l10n("en")
USER = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _reminder(recurring: bool, series_in: timedelta) -> SimpleNamespace:
    return SimpleNamespace(
        id=5, user_id=1, execution_time=_now() + series_in, is_recurring=recurring, is_habit=False,
        is_fluid_habit=False, is_nagging=False, last_nag_chat_id=None, last_nag_message_id=None,
        snooze_count=0, snoozed_until=None,
    )


async def _quick(action: str, reminder) -> str:
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)
    sched = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None, schedule_snooze=lambda *a, **k: None,
        remove_nagging_job=lambda rid: None,
    )
    cb = SimpleNamespace(
        data=f"snooze_act_5_{action}",
        message=SimpleNamespace(text="Standup", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )
    await callback_snooze_act(cb, dao, sched, SimpleNamespace(), USER, L10N)
    return cb.message.edit_text.await_args.args[0]


async def test_snooze_past_the_next_occurrence_names_both_times() -> None:
    text = await _quick("1d", _reminder(True, timedelta(hours=5)))
    assert "Snoozed until" in text
    assert "Next scheduled repeat" in text


async def test_snooze_before_the_next_occurrence_has_no_extra_line() -> None:
    text = await _quick("1h", _reminder(True, timedelta(hours=20)))
    assert "Snoozed until" in text
    assert "Next scheduled repeat" not in text


async def test_one_off_task_has_no_extra_line() -> None:
    text = await _quick("1d", _reminder(False, timedelta(hours=5)))
    assert "Next scheduled repeat" not in text


def test_helper_escapes_markdown_v2_in_the_time() -> None:
    line = series_next_line(_reminder(True, timedelta(hours=5)), _now() + timedelta(days=2), USER, L10N)
    assert line and "\\." in line  # the dot of DD.MM is escaped for MarkdownV2


async def test_custom_snooze_confirmation_names_the_next_occurrence() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.commit()
    dao = ReminderDAO(session)
    r = await dao.create_reminder(
        1, "standup", _now() + timedelta(hours=5), is_recurring=True, rrule_string="FREQ=DAILY",
    )
    await session.commit()
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.update_data(
        text="standup", edit_reminder_id=r.id, is_snooze_mode=True,
        execution_time=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
    )
    msg = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())
    sched = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None, schedule_snooze=lambda *a, **k: None,
        remove_snooze_job=lambda rid: None, remove_nagging_job=lambda rid: None,
    )

    await _save_and_show_edit(msg, state, L10N, USER, dao, sched)

    assert "Next scheduled repeat" in msg.answer.await_args.args[0]
    await engine.dispose()
