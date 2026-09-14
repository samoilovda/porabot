"""3.5: a scheduling failure for ONE task in a recovery bulk action
("Done all"/"Snooze all") used to roll back the WHOLE DB transaction while
leaving every EARLIER task's job in the batch already mutated (removed/
rescheduled) on the live scheduler outside that transaction — rollback()
only undoes the session, not scheduler side effects already applied.
Result: those earlier tasks' jobs were left out of sync with their
(reverted) DB rows.

The fix restructures both handlers so nothing touches the scheduler until
AFTER a successful commit — proven here by a scheduling failure on the
SECOND of two tasks, checking the first task's DB row was still marked
done (the commit succeeded) and its job was scheduled correctly, not
left dangling from a rolled-back-then-abandoned mid-loop attempt.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.reminders_listing import callback_recovery_done_all, callback_recovery_snooze_all
from bot.lexicon import get_l10n


async def test_done_all_second_task_schedule_failure_does_not_strand_the_first() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    tasks = [
        SimpleNamespace(
            id=1, user_id=1, execution_time=now, is_recurring=True, rrule_string="FREQ=DAILY", is_nagging=False,
            is_habit=False, is_fluid_habit=False, habit_active_due_at=None, completed_for_execution_time=None,
        ),
        SimpleNamespace(
            id=2, user_id=1, execution_time=now, is_recurring=True, rrule_string="FREQ=DAILY", is_nagging=False,
            is_habit=False, is_fluid_habit=False, habit_active_due_at=None, completed_for_execution_time=None,
        ),
    ]
    reminder_dao = SimpleNamespace(
        get_overdue_pending_tasks=AsyncMock(return_value=tasks),
        mark_done=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    habit_event_dao = SimpleNamespace()

    scheduled_ids: list[int] = []

    def _schedule_reminder(task_id, dt, is_nagging=False):
        if task_id == 2:
            raise RuntimeError("scheduler exploded on task 2")
        scheduled_ids.append(task_id)

    scheduler_service = SimpleNamespace(
        schedule_reminder=_schedule_reminder,
        remove_reminder_job=lambda rid: None,
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))

    await callback_recovery_done_all(callback, reminder_dao, habit_event_dao, scheduler_service, user, l10n)

    # The commit succeeded (both tasks' mark_done went through durably)
    # despite task 2's scheduling failure — rollback must NOT have fired.
    reminder_dao.session.commit.assert_awaited_once()
    reminder_dao.session.rollback.assert_not_awaited()
    assert reminder_dao.mark_done.await_count == 2
    # Task 1's job was scheduled fine; task 2's failure didn't undo it or
    # leave it stranded.
    assert scheduled_ids == [1]
    # The whole batch is still reported as done to the user — a partial
    # scheduler failure for one task doesn't turn into a whole-batch error.
    callback.message.edit_text.assert_awaited_once()


async def test_snooze_all_second_task_schedule_failure_does_not_strand_the_first() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    tasks = [
        SimpleNamespace(
            id=1, user_id=1, execution_time=now, is_recurring=False, is_nagging=False,
            is_habit=False, is_fluid_habit=False, completed_for_execution_time=None,
            last_nag_chat_id=None, last_nag_message_id=None,
        ),
        SimpleNamespace(
            id=2, user_id=1, execution_time=now, is_recurring=False, is_nagging=False,
            is_habit=False, is_fluid_habit=False, completed_for_execution_time=None,
            last_nag_chat_id=None, last_nag_message_id=None,
        ),
    ]
    reminder_dao = SimpleNamespace(
        get_overdue_pending_tasks=AsyncMock(return_value=tasks),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )

    scheduled_ids: list[int] = []

    def _schedule_reminder(task_id, dt, is_nagging=False):
        if task_id == 2:
            raise RuntimeError("scheduler exploded on task 2")
        scheduled_ids.append(task_id)

    scheduler_service = SimpleNamespace(schedule_reminder=_schedule_reminder, remove_nagging_job=lambda rid: None)
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))

    await callback_recovery_snooze_all(callback, reminder_dao, scheduler_service, user, l10n)

    reminder_dao.session.commit.assert_awaited_once()
    reminder_dao.session.rollback.assert_not_awaited()
    # Both rows still got their new execution_time set (the commit covers both).
    assert tasks[0].execution_time != now
    assert tasks[1].execution_time != now
    assert scheduled_ids == [1]
    callback.message.edit_text.assert_awaited_once()
