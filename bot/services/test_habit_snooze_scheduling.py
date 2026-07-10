import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


async def test_habit_custom_snooze_schedules_job_at_chosen_time_not_stale_db_time() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    old_execution_time = datetime(2026, 5, 1, 9, 0, 0)
    chosen_time = datetime.now().replace(microsecond=0) + timedelta(hours=2)

    reminder = SimpleNamespace(
        id=5,
        reminder_text="Workout",
        execution_time=old_execution_time,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_fluid_habit=False,
        is_nagging=False,
        nagging_max_repeats=3,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=2,
        habit_streak_best=5,
    )
    reminder_dao = SimpleNamespace(
        get_by_id=AsyncMock(return_value=reminder),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=Mock())

    state = _make_state()
    await state.update_data(
        text="Workout",
        execution_time=chosen_time.isoformat(),
        edit_reminder_id=5,
        is_snooze_mode=True,
        user_timezone="UTC",
        chat_id=1,
    )

    user = SimpleNamespace(timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("ru")
    source_message = SimpleNamespace(answer=AsyncMock())

    await reminders_module._save_and_show_edit(
        source_message, state, l10n, user, reminder_dao, scheduler_service
    )

    # DB execution_time stays untouched (anti-drift guard for habit recurring snooze).
    assert reminder.execution_time == old_execution_time

    # But the actual APScheduler job must fire at the time the user picked.
    scheduler_service.schedule_reminder.assert_called_once()
    scheduled_id, scheduled_run_date = scheduler_service.schedule_reminder.call_args.args
    assert scheduled_id == 5
    assert scheduled_run_date.replace(tzinfo=None) == chosen_time
