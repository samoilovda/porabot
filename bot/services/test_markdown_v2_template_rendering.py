import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_list_line_escapes_only_user_data_not_backticks() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    task = SimpleNamespace(
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=False,
        is_nagging=False,
        reminder_text="buy *milk*.",
    )
    user = SimpleNamespace(timezone="UTC", show_utc_offset=False)

    line = reminders_module._format_task_line_md2(task, user)

    # Template backticks around the time stay literal (real code-span markup)...
    assert line.startswith("▫️ `")
    # ...but the user's own asterisks and dot are escaped so they render as text.
    assert "\\*milk\\*\\." in line


async def test_task_saved_preview_keeps_template_bold_but_escapes_task_text() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    future_time = datetime.now().replace(microsecond=0) + timedelta(hours=2)
    reminder = SimpleNamespace(
        id=1,
        reminder_text="call *mom*",
        execution_time=future_time,
        is_recurring=False,
        is_nagging=False,
        nagging_max_repeats=3,
    )
    reminder_dao = SimpleNamespace(
        get_by_id=AsyncMock(return_value=reminder),
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.update_data(
        text="call *mom*",
        execution_time=future_time.isoformat(),
        edit_reminder_id=1,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("ru")
    message = SimpleNamespace(answer=AsyncMock())

    await reminders_module._save_and_show_edit(message, state, l10n, user, reminder_dao, scheduler_service)

    sent_text = message.answer.await_args.args[0]
    # Lexicon's own *bold* markers around "Задача сохранена!" are untouched...
    assert "*Задача сохранена\\!*" in sent_text
    # ...while the user's literal asterisks around "mom" are escaped.
    assert "call \\*mom\\*" in sent_text
