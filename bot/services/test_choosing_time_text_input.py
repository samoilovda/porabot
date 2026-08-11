import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


async def test_typed_time_while_choosing_time_creates_the_reminder() -> None:
    """Regression (REWORK_PLAN_3 2.1): ReminderWizard.choosing_time had no
    message handler at all — a user who typed a time instead of tapping a
    button (e.g. "в 18:30") got no response whatsoever, with no way out
    short of /cancel or a menu button.
    """
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    created_reminder = SimpleNamespace(
        id=1,
        reminder_text="call mom",
        is_recurring=False,
        is_nagging=False,
        nagging_max_repeats=3,
        rrule_string=None,
    )
    reminder_dao = SimpleNamespace(
        create_reminder=AsyncMock(return_value=created_reminder),
        get_owned=AsyncMock(),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())

    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="call mom")

    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(
        text="в 18:30",
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=99, chat=SimpleNamespace(id=1))),
    )

    await reminders_module.state_choosing_time_text_input(
        message, state, user, l10n, reminder_dao, scheduler_service
    )

    reminder_dao.create_reminder.assert_awaited_once()
    kwargs = reminder_dao.create_reminder.await_args.kwargs
    assert kwargs["text"] == "call mom"
    assert kwargs["execution_time"].hour == 18
    assert kwargs["execution_time"].minute == 30
    scheduler_service.schedule_reminder.assert_called_once()
    assert await state.get_state() is None  # cleared by _save_and_show_edit on success


async def test_unparseable_text_while_choosing_time_reprompts_without_losing_state() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    reminder_dao = SimpleNamespace(create_reminder=AsyncMock(), get_owned=AsyncMock())
    scheduler_service = SimpleNamespace(schedule_reminder=AsyncMock())

    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="call mom")

    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(text="абырвалг", answer=AsyncMock())

    await reminders_module.state_choosing_time_text_input(
        message, state, user, l10n, reminder_dao, scheduler_service
    )

    reminder_dao.create_reminder.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == l10n["choosing_time_retry"]
    # State (and the task text within it) must survive — this is not a dead end.
    assert await state.get_state() == ReminderWizard.choosing_time.state
    data = await state.get_data()
    assert data["text"] == "call mom"
