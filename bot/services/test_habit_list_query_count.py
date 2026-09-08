"""Regression test for 2.1: cb_habit_list must fetch habit events with ONE
batched query, not one query per habit (the exact N+1 shape already fixed
once for the Mini App's /api/miniapp/scores — see fix(3.2) and
bot/services/test_miniapp_scores_query_count.py, whose pattern this
mirrors).
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
    spec = importlib.util.spec_from_file_location("test_module_query_count_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixed_habit(id_):
    return SimpleNamespace(
        id=id_,
        status="pending",
        reminder_text=f"Habit {id_}",
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_habit=True,
        is_fluid_habit=False,
        is_recurring=True,
        is_nagging=True,
        rrule_string="FREQ=DAILY",
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=1,
        habit_streak_best=1,
    )


async def test_habit_list_makes_one_events_query_regardless_of_habit_count() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    habits = [_fixed_habit(i) for i in range(1, 16)]
    reminder_dao = SimpleNamespace(
        get_user_reminders=AsyncMock(return_value=habits),
        get_active_fluid_habits=AsyncMock(return_value=[]),
    )
    get_events_for_reminder = AsyncMock(return_value=[])  # must never be called (per-habit N+1 shape)
    get_events_for_reminders = AsyncMock(return_value={h.id: [] for h in habits})  # the batched shape
    habit_event_dao = SimpleNamespace(
        get_events_for_reminder=get_events_for_reminder,
        get_events_for_reminders=get_events_for_reminders,
    )
    user = SimpleNamespace(id=99, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await habits_module.cb_habit_list(callback, user, reminder_dao, habit_event_dao, l10n)

    get_events_for_reminders.assert_awaited_once()
    called_ids = get_events_for_reminders.await_args.args[0]
    assert sorted(called_ids) == [h.id for h in habits]
    get_events_for_reminder.assert_not_called()
