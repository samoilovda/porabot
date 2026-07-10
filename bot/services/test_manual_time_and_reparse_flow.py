import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


async def test_manual_time_entry_clears_state_so_next_text_is_not_ignored() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)

    callback = SimpleNamespace(
        data="time_manual",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    user = SimpleNamespace(timezone="UTC")
    l10n = {"try_again_manual": "Type the whole task again."}

    await reminders_module.callback_time_selected(
        callback, state, user, l10n, reminder_dao=None, scheduler_service=None
    )

    assert await state.get_state() is None
    callback.message.edit_text.assert_awaited_once_with(l10n["try_again_manual"])


async def test_new_text_during_confirming_parse_is_not_ignored() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    state = _make_state()
    await state.set_state(ReminderWizard.confirming_parse)
    await state.update_data(text="old stale task")

    reminders_module.parser.parse = AsyncMock(
        return_value=SimpleNamespace(clean_text="call mom", parsed_datetime=None, confidence=1.0)
    )

    message = SimpleNamespace(
        text="call mom",
        chat=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )
    user = SimpleNamespace(timezone="UTC", show_utc_offset=False)
    l10n = {"ask_time": "When should I remind you about: {text}?", "parse_error": "error"}

    await reminders_module.state_confirming_parse_new_text(
        message, state, user, l10n, reminder_dao=None, scheduler_service=None
    )

    reminders_module.parser.parse.assert_awaited_once_with("call mom", "UTC")
    assert await state.get_state() == ReminderWizard.choosing_time.state
    data = await state.get_data()
    assert data["text"] == "call mom"
