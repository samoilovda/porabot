"""Regressions for the 2026-10-18 product audit."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers.commands import callback_cancel
from bot.handlers.habits import HabitState, state_habit_time
from bot.keyboards.inline import get_edit_keyboard
from bot.lexicon import get_l10n


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))


def test_existing_task_has_no_creation_cancel():
    buttons = [b for row in get_edit_keyboard(1, get_l10n("ru")).inline_keyboard for b in row]
    assert "cancel_wizard" not in [b.callback_data for b in buttons]


async def test_legacy_cancel_without_wizard_does_not_claim_creation_cancelled():
    callback = SimpleNamespace(
        message=SimpleNamespace(delete=AsyncMock(), edit_reply_markup=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    await callback_cancel(callback, state(), get_l10n("ru"))
    assert callback.message.answer.await_args.args[0] != get_l10n("ru")["cmd_cancel"]


async def test_habit_date_without_hour_does_not_create_midnight_job():
    s = state()
    await s.set_state(HabitState.waiting_for_time)
    await s.update_data(habit_text="Read")
    dao = SimpleNamespace(
        create_reminder=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock()),
        get_habit_motivation_stats=AsyncMock(return_value={}),
    )
    service = Mock()
    result = SimpleNamespace(
        parsed_datetime=datetime.now(timezone.utc) + timedelta(days=5), date_only=True, confidence=0.4
    )
    with patch("bot.handlers.habits.InputParser.parse", AsyncMock(return_value=result)):
        await state_habit_time(
            SimpleNamespace(text="в понедельник", answer=AsyncMock()),
            s,
            SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False),
            dao,
            service,
            get_l10n("ru"),
        )
    dao.create_reminder.assert_not_awaited()
    service.schedule_reminder.assert_not_called()
    assert (await s.get_data())["habit_start_date"] == result.parsed_datetime.date().isoformat()
