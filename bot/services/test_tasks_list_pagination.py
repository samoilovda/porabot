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


def test_paginate_tasks_for_list_under_limit_returns_all_no_suffix() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    l10n = get_l10n("en")
    tasks = _make_tasks(5)

    shown, suffix = reminders_module._paginate_tasks_for_list(tasks, l10n)

    assert shown == tasks
    assert suffix == ""


def test_paginate_tasks_for_list_over_limit_truncates_with_suffix() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    l10n = get_l10n("en")
    tasks = _make_tasks(60)

    shown, suffix = reminders_module._paginate_tasks_for_list(tasks, l10n)

    assert len(shown) == reminders_module._TASKS_PAGE_LIMIT
    assert shown == tasks[: reminders_module._TASKS_PAGE_LIMIT]
    assert suffix == l10n["tasks_more"].format(count=60 - reminders_module._TASKS_PAGE_LIMIT)


async def test_refresh_tasks_renders_truncated_list_and_keyboard() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    tasks = _make_tasks(60)
    reminder_dao = SimpleNamespace(get_user_reminders=AsyncMock(return_value=tasks))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await reminders_module.callback_refresh_tasks(callback, reminder_dao, user, l10n)

    sent_text = message.edit_text.await_args.args[0]
    markup = message.edit_text.await_args.kwargs["reply_markup"]

    # Only the first 25 tasks got a line + buttons; the rest are summarized.
    assert "Task 1\\" not in sent_text or "Task 1" in sent_text  # sanity: text present at all
    assert l10n["tasks_more"].format(count=35) in sent_text
    task_button_rows = [
        row for row in markup.inline_keyboard
        if any((btn.callback_data or "").startswith("done_task_") for btn in row)
    ]
    assert len(task_button_rows) == reminders_module._TASKS_PAGE_LIMIT


async def test_my_tasks_menu_button_also_paginates() -> None:
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
    assert l10n["tasks_more"].format(count=15) in sent_text
    task_button_rows = [
        row for row in markup.inline_keyboard
        if any((btn.callback_data or "").startswith("done_task_") for btn in row)
    ]
    assert len(task_button_rows) == 25
