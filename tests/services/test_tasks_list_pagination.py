import importlib.util
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Loading bot/handlers/menu.py triggers bot/handlers/__init__.py (Python
# always imports the parent package first), which pulls in bot.config via
# admin.py — BOT_TOKEN must be set for that import to succeed. The value
# itself is never used by this test.
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL-ABCDEFGHIJKLMNOPQRS")

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_tasks(count: int):
    return [
        SimpleNamespace(
            id=i,
            execution_time=datetime(2026, 5, 1, 9, 0, 0),
            is_recurring=False,
            is_nagging=False,
            reminder_text=f"Task {i}",
        )
        for i in range(1, count + 1)
    ]


async def test_my_tasks_menu_button_shows_first_page_with_nav() -> None:
    menu_module = _load_module("bot/handlers/menu.py")

    tasks = _make_tasks(40)
    reminder_dao = SimpleNamespace(get_user_reminders=AsyncMock(return_value=tasks))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock())

    await menu_module.btn_my_tasks(message, state, reminder_dao, user, l10n)

    sent_text = message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert "1/5" in sent_text
    task_button_rows = [
        row for row in markup.inline_keyboard
        if any(":done:" in (btn.callback_data or "") for btn in row)
    ]
    assert len(task_button_rows) == 8


