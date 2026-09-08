import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeParser:
    def __init__(self, parsed_datetime):
        self._parsed_datetime = parsed_datetime

    async def parse(self, text, tz):
        return SimpleNamespace(clean_text=text, parsed_datetime=self._parsed_datetime, confidence=1.0)


async def test_habit_created_with_ambiguous_past_time_lands_in_the_future() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    # Parser resolved "at 9" to a time that has already passed today.
    past_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)

    habits_module.InputParser = lambda: _FakeParser(past_local)

    created_reminder = SimpleNamespace(id=1, is_nagging=True)
    reminder_dao = SimpleNamespace(
        create_reminder=AsyncMock(return_value=created_reminder),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=Mock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"habit_text": "Stretch"}),
        clear=AsyncMock(),
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = {
        "habit_default_name": "Habit",
        "habit_created": "Created {habit} at {time}",
        "habit_time_retry": "retry",
        "habit_create_failed_long": "failed",
        "habit_create_failed_internal": "internal error",
    }
    message = SimpleNamespace(text="at 9", answer=AsyncMock())

    await habits_module.state_habit_time(message, state, user, reminder_dao, scheduler_service, l10n)

    reminder_dao.create_reminder.assert_awaited_once()
    passed_execution_time = reminder_dao.create_reminder.await_args.kwargs["execution_time"]

    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert passed_execution_time > now_utc_naive
    # It's still the same daily FREQ=DAILY slot, just moved to the next
    # occurrence — same hour/minute as originally parsed.
    assert passed_execution_time.hour == past_local.hour
    assert passed_execution_time.minute == past_local.minute

    scheduler_service.schedule_reminder.assert_called_once()
    scheduled_run_date = scheduler_service.schedule_reminder.call_args.args[1]
    assert scheduled_run_date.replace(tzinfo=None) > now_utc_naive


async def test_habit_created_with_future_time_is_unaffected() -> None:
    habits_module = _load_module("bot/handlers/habits.py")

    future_local = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    habits_module.InputParser = lambda: _FakeParser(future_local)

    created_reminder = SimpleNamespace(id=2, is_nagging=True)
    reminder_dao = SimpleNamespace(
        create_reminder=AsyncMock(return_value=created_reminder),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=Mock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"habit_text": "Stretch"}),
        clear=AsyncMock(),
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = {
        "habit_default_name": "Habit",
        "habit_created": "Created {habit} at {time}",
        "habit_time_retry": "retry",
        "habit_create_failed_long": "failed",
        "habit_create_failed_internal": "internal error",
    }
    message = SimpleNamespace(text="in 2 hours", answer=AsyncMock())

    await habits_module.state_habit_time(message, state, user, reminder_dao, scheduler_service, l10n)

    passed_execution_time = reminder_dao.create_reminder.await_args.kwargs["execution_time"]
    assert passed_execution_time == future_local
