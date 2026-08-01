import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.reminder import ReminderDAO
from bot.keyboards.inline import get_evening_wrapup_keyboard, get_task_done_keyboard

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_dao(reminder):
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]
    return dao, session


async def test_habit_streak_counts_completion_within_24h() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    completed = due + timedelta(hours=23, minutes=59)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_last_completed_due_at=None,
    )
    dao, session = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=completed,
    )

    assert result == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1
    assert reminder.habit_streak_best == 1
    assert reminder.habit_last_completed_due_at == due
    session.flush.assert_awaited()


async def test_habit_streak_increments_for_next_daily_cycle() -> None:
    prev_due = datetime(2026, 5, 1, 9, 0, 0)
    due = prev_due + timedelta(days=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=1,
        habit_streak_best=1,
        habit_last_completed_due_at=prev_due,
    )
    dao, _ = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=4),
    )

    assert result == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 2
    assert reminder.habit_streak_best == 2
    assert reminder.habit_last_completed_due_at == due


async def test_habit_streak_resets_when_completion_is_late() -> None:
    prev_due = datetime(2026, 5, 1, 9, 0, 0)
    due = prev_due + timedelta(days=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=4,
        habit_streak_best=7,
        habit_last_completed_due_at=prev_due,
    )
    dao, _ = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=24, minutes=1),
    )

    assert result == {"already_counted": False, "counted": False, "late": True}
    assert reminder.habit_streak_current == 0
    assert reminder.habit_streak_best == 7
    assert reminder.habit_last_completed_due_at == prev_due


async def test_non_habit_daily_reminder_does_not_mutate_streak() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    reminder = SimpleNamespace(
        is_habit=False,
        is_recurring=True,
        is_nagging=False,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
    )
    dao, session = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=1),
    )

    assert result == {"already_counted": False, "counted": False, "late": False}
    session.flush.assert_not_awaited()


async def test_fluid_habit_day_based_streak_updates() -> None:
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    reminder = SimpleNamespace(
        is_fluid_habit=True,
        fluid_streak_current=3,
        fluid_streak_best=3,
        fluid_last_completed_date=yesterday,
        completed_at=None,
    )
    dao, session = _make_dao(reminder)

    done = await dao.mark_fluid_habit_done_today(1, "UTC")

    assert done is True
    assert reminder.fluid_streak_current == 4
    assert reminder.fluid_streak_best == 4
    assert reminder.fluid_last_completed_date == today.isoformat()
    session.flush.assert_awaited()


def test_done_button_can_embed_cycle_due_timestamp() -> None:
    markup = get_task_done_keyboard(123, {"btn_done": "Done", "snooze_15m": "+15m", "snooze_30m": "+30m", "snooze_1h": "+1h", "snooze_2h": "+2h", "snooze_1d": "+1d", "snooze_custom": "Custom"}, cycle_due_ts=1700000000)
    first_button = markup.inline_keyboard[0][0]
    assert first_button.callback_data == "done_task_123_1700000000"


def test_evening_wrapup_keyboard_groups_task_done_and_not_done_in_one_row() -> None:
    task = SimpleNamespace(id=123, reminder_text="Read before bed")

    markup = get_evening_wrapup_keyboard(
        [task],
        {"btn_done_short": "Done", "btn_not_done_short": "Not done"},
    )

    row = markup.inline_keyboard[0]
    assert [button.text for button in row] == ["Read before bed", "Done", "Not done"]
    assert [button.callback_data for button in row] == [
        "wrap_task_123",
        "wrap_done_123",
        "wrap_not_done_123",
    ]


async def test_recovery_done_all_updates_habit_streak() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    due = datetime(2026, 5, 1, 9, 0, 0)
    task = SimpleNamespace(
        id=1,
        is_habit=True,
        is_fluid_habit=False,
        is_recurring=True,
        is_nagging=False,
        rrule_string="FREQ=DAILY",
        execution_time=due,
        habit_active_due_at=due,
        habit_last_completed_due_at=None,
        habit_streak_current=2,
        habit_streak_best=5,
    )
    reminder_dao = SimpleNamespace(
        get_overdue_pending_tasks=AsyncMock(return_value=[task]),
        apply_habit_streak_completion=AsyncMock(return_value={"already_counted": False, "counted": True}),
        mark_done=AsyncMock(),
        session=SimpleNamespace(rollback=AsyncMock()),
    )
    habit_event_dao = SimpleNamespace(record=AsyncMock(return_value=True))
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None,
        remove_reminder_job=lambda *a, **k: None,
        remove_nagging_job=lambda *a, **k: None,
    )
    user = SimpleNamespace(id=42, timezone="UTC")
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    l10n = {"recovery_done_all_done": "✅ Marked {count} overdue tasks as done."}

    await reminders_module.callback_recovery_done_all(
        callback, reminder_dao, habit_event_dao, scheduler_service, user, l10n
    )

    reminder_dao.apply_habit_streak_completion.assert_awaited_once()
    call = reminder_dao.apply_habit_streak_completion.await_args
    assert call.args[0] == 1
    assert call.kwargs["due_at_utc_naive"] == due
    reminder_dao.mark_done.assert_awaited_once_with(1)

    # P1-5: weekly/monthly reports are built from habit_events, not the
    # streak counters — "Done all" must record one too.
    habit_event_dao.record.assert_awaited_once()
    record_call = habit_event_dao.record.await_args
    assert record_call.kwargs["reminder"] is task
    assert record_call.kwargs["outcome"] == "done"
    assert record_call.kwargs["source"] == "recovery"
    assert record_call.kwargs["due_at_utc_naive"] == due
