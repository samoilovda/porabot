import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytz

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_handler(module_rel_path: str, fn_name: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location(f"test_{fn_name}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, fn_name)


cb_habit_list = _load_handler("bot/handlers/habits.py", "cb_habit_list")


def _fixed_habit(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        status="pending",
        is_habit=True,
        is_fluid_habit=False,
        is_recurring=True,
        is_nagging=True,
        rrule_string="FREQ=DAILY",
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
        fluid_streak_current=0,
        fluid_streak_best=0,
        fluid_mode=None,
        fluid_planned_date=None,
        fluid_planned_time=None,
        reminder_text="Habit",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _run_habit_list(habit) -> str:
    l10n = get_l10n("en")
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    fixed = [habit] if not habit.is_fluid_habit else []
    fluid = [habit] if habit.is_fluid_habit else []
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=fixed),
        get_active_fluid_habits=AsyncMock(return_value=fluid),
    )
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="habit_list", message=message, answer=AsyncMock())

    await cb_habit_list(callback=callback, user=user, reminder_dao=reminder_dao, l10n=l10n)

    return message.edit_text.await_args.args[0]


async def test_fluid_habit_shows_planned_time_for_today() -> None:
    # N5: fluid_planned_time was written but never displayed anywhere.
    today_str = datetime.now(pytz.UTC).date().isoformat()
    habit = _fixed_habit(
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="ask_time",
        fluid_planned_date=today_str,
        fluid_planned_time="18:30",
    )

    text = await _run_habit_list(habit)

    assert "18:30" in text
    assert "anytime today" not in text


async def test_fluid_habit_shows_anytime_when_no_plan_for_today() -> None:
    habit = _fixed_habit(
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="ask_time",
        fluid_planned_date="2000-01-01",
        fluid_planned_time="18:30",
    )

    text = await _run_habit_list(habit)

    assert "anytime today" in text
    assert "18:30" not in text
