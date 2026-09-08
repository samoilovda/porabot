import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


async def test_save_and_show_edit_rejects_past_time_and_reopens_time_picker() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    reminder_dao = SimpleNamespace(
        create_reminder=AsyncMock(),
        get_by_id=AsyncMock(),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=AsyncMock())

    state = _make_state()
    await state.update_data(text="call mom", execution_time=past_time.isoformat())

    user = SimpleNamespace(timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(answer=AsyncMock())

    await reminders_module._save_and_show_edit(
        message, state, l10n, user, reminder_dao, scheduler_service
    )

    reminder_dao.create_reminder.assert_not_awaited()
    scheduler_service.schedule_reminder.assert_not_called()
    assert await state.get_state() == ReminderWizard.choosing_time.state
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == l10n["time_in_past"]
