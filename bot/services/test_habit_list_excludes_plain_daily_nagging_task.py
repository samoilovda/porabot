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


def _plain_daily_nagging_task(id_):
    """A perfectly ordinary task the user turned into daily+nagging through
    the edit keyboard — is_habit stays False, since it was never created
    through the Habits flow."""
    return SimpleNamespace(
        id=id_,
        status="pending",
        reminder_text="water the plants",
        execution_time=datetime(2030, 1, 1, 9, 0, 0),
        is_habit=False,
        is_fluid_habit=False,
        is_recurring=True,
        is_nagging=True,
        rrule_string="FREQ=DAILY",
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
    )


def test_is_habit_entry_rejects_plain_daily_nagging_task() -> None:
    """Regression (REWORK_PLAN_3 1.2): matching on "daily recurring +
    nagging" alone put an ordinary task in "My Habits" — those are just two
    edit-keyboard toggles, not evidence the task went through the Habits
    creation flow."""
    habits_module = _load_module("bot/handlers/habits.py")

    assert habits_module._is_habit_entry(_plain_daily_nagging_task(1)) is False


async def test_habit_list_does_not_show_plain_daily_nagging_task() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    plain_task = _plain_daily_nagging_task(1)
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=[plain_task]),
        get_active_fluid_habits=AsyncMock(return_value=[]),
    )
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, l10n)

    # No habits qualify — must land on the "no active habits" branch, not
    # list the plain task.
    sent_text = message.edit_text.await_args.args[0]
    assert sent_text == l10n["habit_no_active"]
