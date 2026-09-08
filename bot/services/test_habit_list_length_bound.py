"""Regression test for 1.5: cb_habit_list must not exceed Telegram's
message-length / keyboard-button-count limits when a user has many
habits (ReminderDAO.MAX_ACTIVE_HABITS allows up to 50).
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_bound_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixed_habit(id_, text="Habit"):
    return SimpleNamespace(
        id=id_,
        status="pending",
        reminder_text=text,
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_habit=True,
        is_fluid_habit=False,
        is_recurring=True,
        is_nagging=True,
        rrule_string="FREQ=DAILY",
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=2,
        habit_streak_best=5,
    )


async def test_habit_list_caps_rendered_items_and_notes_the_rest() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    many_habits = [_fixed_habit(i, text=f"Habit number {i}") for i in range(1, 41)]  # over the 25-item cap
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=many_habits),
        get_active_fluid_habits=AsyncMock(return_value=[]),
    )
    habit_event_dao = SimpleNamespace(get_events_for_reminder=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, habit_event_dao, l10n)

    message.edit_text.assert_awaited_once()
    text = message.edit_text.await_args.args[0]
    markup = message.edit_text.await_args.kwargs["reply_markup"]

    assert len(text) < 4096
    shown_count = sum(1 for row in markup.inline_keyboard if any("del_habit_" in (b.callback_data or "") for b in row))
    assert shown_count == habits_module._HABIT_LIST_LIMIT
    assert "Habit number 1\n" in text or text.count("Habit number") == habits_module._HABIT_LIST_LIMIT
    # Hidden-count notice present.
    assert l10n.get("brief_items_more", "…and {count} more").format(
        count=len(many_habits) - habits_module._HABIT_LIST_LIMIT
    ) in text


async def test_habit_list_truncates_a_very_long_habit_text() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    long_text = "x" * 3000
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=[_fixed_habit(1, text=long_text)]),
        get_active_fluid_habits=AsyncMock(return_value=[]),
    )
    habit_event_dao = SimpleNamespace(get_events_for_reminder=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, habit_event_dao, l10n)

    text = message.edit_text.await_args.args[0]
    assert long_text not in text
    assert len(text) < 4096
