"""Unit tests for ReminderDAO — get_user_reminders, mark_done, create_reminder."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import ReminderStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dao(reminder=None):
    """Return a (dao, session) pair with get_by_id mocked."""
    session = SimpleNamespace(flush=AsyncMock(), add=lambda x: None)
    dao = ReminderDAO(session)       # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)   # type: ignore[method-assign]
    return dao, session


def _pending_reminder(*, is_recurring=False, rrule_string=None):
    return SimpleNamespace(
        id=1,
        user_id=100,
        status=ReminderStatus.PENDING,
        is_recurring=is_recurring,
        rrule_string=rrule_string,
        execution_time=datetime(2026, 6, 7, 9, 0, 0),
        completed_for_execution_time=None,
        completed_at=None,
        last_completion_note=None,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        is_habit=False,
        is_fluid_habit=False,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
    )


# ---------------------------------------------------------------------------
# create_reminder validation
# ---------------------------------------------------------------------------

async def test_create_reminder_rejects_overlong_text():
    session = SimpleNamespace(flush=AsyncMock(), add=lambda x: None)
    dao = ReminderDAO(session)   # type: ignore[arg-type]
    with pytest.raises(ValueError, match="too long"):
        await dao.create_reminder(
            user_id=1,
            text="x" * 3001,
            execution_time=datetime(2026, 6, 7, 9, 0),
        )


async def test_create_reminder_rejects_negative_nag_repeats():
    session = SimpleNamespace(flush=AsyncMock(), add=lambda x: None)
    dao = ReminderDAO(session)   # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot be negative"):
        await dao.create_reminder(
            user_id=1,
            text="hello",
            execution_time=datetime(2026, 6, 7, 9, 0),
            nagging_max_repeats=-1,
        )


# ---------------------------------------------------------------------------
# mark_done — one-time reminder
# ---------------------------------------------------------------------------

async def test_mark_done_one_time_sets_completed():
    reminder = _pending_reminder()
    dao, session = _make_dao(reminder)

    await dao.mark_done(1)

    assert reminder.status == ReminderStatus.COMPLETED
    assert reminder.completed_at is not None
    assert reminder.completed_for_execution_time == reminder.execution_time
    assert reminder.last_nag_chat_id is None
    session.flush.assert_awaited_once()


async def test_mark_done_one_time_clears_nag_fields():
    reminder = _pending_reminder()
    reminder.last_nag_chat_id = 123
    reminder.last_nag_message_id = 456
    dao, _ = _make_dao(reminder)

    await dao.mark_done(1)

    assert reminder.last_nag_chat_id is None
    assert reminder.last_nag_message_id is None


# ---------------------------------------------------------------------------
# mark_done — recurring reminder (execution_time in the past = already fired)
# ---------------------------------------------------------------------------

async def test_mark_done_recurring_past_execution_stays_pending():
    reminder = _pending_reminder(is_recurring=True, rrule_string="FREQ=DAILY")
    # execution_time is in the past — scheduler has not rolled forward yet
    reminder.execution_time = datetime(2026, 1, 1, 9, 0, 0)   # well in the past
    dao, session = _make_dao(reminder)

    await dao.mark_done(1)

    assert reminder.status == ReminderStatus.PENDING
    # completed_for_execution_time should be set so the task hides from active list
    assert reminder.completed_for_execution_time is not None
    session.flush.assert_awaited_once()


async def test_mark_done_recurring_future_execution_does_not_hide():
    reminder = _pending_reminder(is_recurring=True, rrule_string="FREQ=DAILY")
    # execution_time is far in the future (scheduler already rolled it forward)
    reminder.execution_time = datetime(2099, 12, 31, 9, 0, 0)
    dao, _ = _make_dao(reminder)

    await dao.mark_done(1)

    assert reminder.status == ReminderStatus.PENDING
    # Should NOT hide the future cycle
    assert reminder.completed_for_execution_time is None


# ---------------------------------------------------------------------------
# mark_done — missing reminder (should be a no-op)
# ---------------------------------------------------------------------------

async def test_mark_done_missing_reminder_is_noop():
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)   # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=None)   # type: ignore[method-assign]

    await dao.mark_done(999)   # should not raise

    session.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_last_completion_note
# ---------------------------------------------------------------------------

async def test_set_last_completion_note_updates_field():
    reminder = _pending_reminder()
    reminder.last_completion_note = None
    dao, session = _make_dao(reminder)

    await dao.set_last_completion_note(1, "Great job!")

    assert reminder.last_completion_note == "Great job!"
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_execution_time
# ---------------------------------------------------------------------------

async def test_update_execution_time_stores_new_time():
    reminder = _pending_reminder()
    dao, session = _make_dao(reminder)
    new_time = datetime(2026, 6, 8, 9, 0)

    await dao.update_execution_time(1, new_time)

    assert reminder.execution_time == new_time
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# ReminderStatus enum
# ---------------------------------------------------------------------------

def test_reminder_status_values_are_strings():
    assert ReminderStatus.PENDING == "pending"
    assert ReminderStatus.COMPLETED == "completed"


def test_reminder_status_str_comparison():
    assert ReminderStatus.PENDING == ReminderStatus.PENDING
    assert ReminderStatus.PENDING != ReminderStatus.COMPLETED
