import importlib.util
from datetime import datetime
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


def _fixed_habit(id_):
    return SimpleNamespace(
        id=id_,
        status="pending",
        reminder_text="Read 10 pages",
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


def _fluid_habit(id_):
    return SimpleNamespace(
        id=id_,
        status="pending",
        reminder_text="Drink water",
        is_habit=True,
        is_fluid_habit=True,
        is_recurring=True,
        is_nagging=False,
        rrule_string=None,
        fluid_mode="brief_only",
        fluid_streak_current=3,
        fluid_streak_best=3,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
    )


async def test_habit_list_shows_both_fixed_and_fluid_habits() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    fixed = _fixed_habit(1)
    fluid = _fluid_habit(2)
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=[fixed]),
        get_active_fluid_habits=AsyncMock(return_value=[fluid]),
    )
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, l10n)

    message.edit_text.assert_awaited_once()
    rendered_text, kwargs = message.edit_text.await_args.args, message.edit_text.await_args.kwargs
    text = rendered_text[0]
    assert "Read 10 pages" in text
    assert "Drink water" in text

    markup = kwargs["reply_markup"]
    all_callback_data = [
        button.callback_data for row in markup.inline_keyboard for button in row
    ]
    assert "del_habit_1" in all_callback_data
    assert "del_habit_2" in all_callback_data
    # Fluid habits don't get a task_settings gear (they don't use the
    # repeat/nagging toggles that screen exposes).
    assert "task_settings_1" in all_callback_data
    assert "task_settings_2" not in all_callback_data


async def test_habit_list_shows_only_fluid_habit_when_no_fixed_habits_exist() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    fluid = _fluid_habit(7)
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=[]),
        get_active_fluid_habits=AsyncMock(return_value=[fluid]),
    )
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, l10n)

    # Must NOT hit the "no active habits" branch just because the fixed-habit
    # list was empty.
    sent_text = message.edit_text.await_args.args[0]
    assert sent_text != l10n["habit_no_active"]
    assert "Drink water" in sent_text
