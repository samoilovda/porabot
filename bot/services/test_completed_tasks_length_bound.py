"""Regression test for 1.5: callback_show_completed must not exceed
Telegram's 4096-char message limit with a long completed-tasks history,
and must not crash the whole screen if it somehow still does (the
edit_text call is now wrapped in try/except TelegramBadRequest).
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_completed_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed_task(id_, text="Task"):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return SimpleNamespace(
        id=id_,
        reminder_text=text,
        execution_time=now - timedelta(hours=1),
        completed_at=now,
        last_completion_note=None,
    )


async def test_completed_tasks_caps_rendered_items_and_notes_the_rest() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    many_completed = [_completed_task(i, text=f"Task number {i}") for i in range(1, 61)]
    reminder_dao = SimpleNamespace(get_recent_completed_tasks=AsyncMock(return_value=many_completed))
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await reminders_module.callback_show_completed(callback, reminder_dao, user, l10n)

    message.edit_text.assert_awaited_once()
    text = message.edit_text.await_args.args[0]
    assert len(text) < 4096
    assert text.count("Task number") == reminders_module._COMPLETED_HISTORY_LIMIT
    assert l10n.get("brief_items_more", "…and {count} more").format(
        count=len(many_completed) - reminders_module._COMPLETED_HISTORY_LIMIT
    ) in text


async def test_completed_tasks_truncates_a_very_long_task_text_and_note() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    task = _completed_task(1, text="x" * 3000)
    task.last_completion_note = "y" * 400
    reminder_dao = SimpleNamespace(get_recent_completed_tasks=AsyncMock(return_value=[task]))
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await reminders_module.callback_show_completed(callback, reminder_dao, user, l10n)

    text = message.edit_text.await_args.args[0]
    assert "x" * 3000 not in text
    assert "y" * 400 not in text
    assert len(text) < 4096


async def test_completed_tasks_survives_edit_text_raising_bad_request() -> None:
    """Even if the length bound above somehow isn't enough for a given
    edge case, a TelegramBadRequest from edit_text must not propagate and
    crash the handler — the callback still gets answered."""
    reminders_module = _load_module("bot/handlers/reminders.py")

    reminder_dao = SimpleNamespace(get_recent_completed_tasks=AsyncMock(return_value=[_completed_task(1)]))
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method=None, message="too long")))
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await reminders_module.callback_show_completed(callback, reminder_dao, user, l10n)

    callback.answer.assert_awaited()
