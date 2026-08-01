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


def test_paginate_tasks_for_list_under_limit_returns_all_single_page() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    tasks = _make_tasks(5)

    shown, page, total_pages = reminders_module._paginate_tasks_for_list(tasks)

    assert shown == tasks
    assert page == 0
    assert total_pages == 1


def test_paginate_tasks_for_list_over_limit_splits_into_pages() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    tasks = _make_tasks(60)

    page0, page, total_pages = reminders_module._paginate_tasks_for_list(tasks, page=0)
    assert page == 0
    assert total_pages == 3  # 25 + 25 + 10
    assert page0 == tasks[:25]

    page1, page, _ = reminders_module._paginate_tasks_for_list(tasks, page=1)
    assert page == 1
    assert page1 == tasks[25:50]

    page2, page, _ = reminders_module._paginate_tasks_for_list(tasks, page=2)
    assert page == 2
    assert page2 == tasks[50:60]


def test_paginate_tasks_for_list_clamps_out_of_range_page() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    tasks = _make_tasks(30)

    shown, page, total_pages = reminders_module._paginate_tasks_for_list(tasks, page=99)

    assert page == total_pages - 1 == 1
    assert shown == tasks[25:30]


async def test_tasks_page_callback_renders_requested_page_and_nav_buttons() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    tasks = _make_tasks(60)
    reminder_dao = SimpleNamespace(get_user_reminders=AsyncMock(return_value=tasks))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tasks_page_1", message=message, answer=AsyncMock())

    await reminders_module.callback_tasks_page(callback, reminder_dao, user, l10n)

    sent_text = message.edit_text.await_args.args[0]
    markup = message.edit_text.await_args.kwargs["reply_markup"]

    assert "Task 26" in sent_text  # second page starts at the 26th task
    assert l10n.get("tasks_page_indicator", "📄 {page}/{total}").format(page=2, total=3) in sent_text

    task_button_rows = [
        row for row in markup.inline_keyboard
        if any((btn.callback_data or "").startswith("done_task_") for btn in row)
    ]
    assert len(task_button_rows) == 25

    nav_row = next(
        row for row in markup.inline_keyboard
        if any((btn.callback_data or "").startswith("tasks_page_") for btn in row)
    )
    nav_targets = {btn.callback_data for btn in nav_row}
    assert "tasks_page_0" in nav_targets  # Prev
    assert "tasks_page_2" in nav_targets  # Next


async def test_tasks_page_callback_out_of_range_clamps_to_last_page() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    tasks = _make_tasks(30)
    reminder_dao = SimpleNamespace(get_user_reminders=AsyncMock(return_value=tasks))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tasks_page_99", message=message, answer=AsyncMock())

    await reminders_module.callback_tasks_page(callback, reminder_dao, user, l10n)

    sent_text = message.edit_text.await_args.args[0]
    assert "Task 30" in sent_text
    assert "Task 1\n" not in sent_text  # page 0's first task isn't on this page


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
    assert l10n.get("tasks_page_indicator", "📄 {page}/{total}").format(page=1, total=2) in sent_text
    task_button_rows = [
        row for row in markup.inline_keyboard
        if any((btn.callback_data or "").startswith("done_task_") for btn in row)
    ]
    assert len(task_button_rows) == 25


async def test_refresh_button_targets_the_current_page_not_page_zero() -> None:
    """P1-12 acceptance: an action taken on a later page must not bounce the
    user back to page 1 — the Refresh button's callback_data encodes the
    page it was rendered on."""
    reminders_module = _load_module("bot/handlers/reminders.py")
    get_tasks_list_keyboard = reminders_module.get_tasks_list_keyboard

    tasks = _make_tasks(10)
    l10n = get_l10n("en")

    markup = get_tasks_list_keyboard(tasks, l10n, page=2, total_pages=5)

    refresh_row = next(
        row for row in markup.inline_keyboard
        if any(btn.callback_data == "tasks_page_2" for btn in row)
    )
    assert any(btn.text == l10n["btn_refresh"] for btn in refresh_row)
