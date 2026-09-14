"""2.2: several more handlers reschedule a job BEFORE the DB write that
describes it is durable — same class of bug as 1.3, different call sites.
Each must commit explicitly and, on failure, restore a job consistent
with the actual (rolled-back) DB state.

Covers _apply_repeat_change (the single choke point every rrb_* repeat-
builder callback AND callback_edit_nagging's sibling logic goes through),
callback_undo_delete, and the recovery-digest bulk actions.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.reminders_listing import (
    callback_recovery_done_all,
    callback_recovery_snooze_all,
    callback_undo_delete,
)
from bot.handlers.reminders_shared import _apply_repeat_change
from bot.lexicon import get_l10n


def _failing_commit():
    async def _fail():
        raise RuntimeError("database is locked")
    return _fail


async def test_apply_repeat_change_commit_failure_restores_prior_job() -> None:
    old_time = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) + timedelta(hours=1)
    reminder = SimpleNamespace(
        id=3, execution_time=old_time, is_recurring=False, rrule_string=None, is_nagging=False,
    )
    session = SimpleNamespace(
        flush=AsyncMock(),
        commit=_failing_commit(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )
    reminder_dao = SimpleNamespace(session=session)

    scheduled_calls: list = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append(dt),
        remove_reminder_job=lambda rid: scheduled_calls.append("REMOVED"),
    )
    user = SimpleNamespace(id=1, timezone="UTC")

    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, True, "FREQ=DAILY")

    assert ok is False
    # Scheduled once for the new rule (now rolled back), once more to
    # restore the prior job — never just abandoned.
    assert len(scheduled_calls) == 2
    assert "REMOVED" not in scheduled_calls


async def test_undo_delete_commit_failure_removes_job_not_leaves_it_dangling() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reminder = SimpleNamespace(
        id=11,
        user_id=1,
        execution_time=now + timedelta(hours=1),
        pending_delete_at=now + timedelta(seconds=3),
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        reminder_text="task",
    )
    session = SimpleNamespace(
        flush=AsyncMock(),
        commit=_failing_commit(),
        rollback=AsyncMock(),
    )
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)

    removed_ids: list[int] = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: None,
        remove_reminder_job=lambda rid: removed_ids.append(rid),
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(
        data="undo_del_11",
        message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1),
        answer=AsyncMock(),
    )

    await callback_undo_delete(callback, reminder_dao, scheduler_service, user, l10n)

    # The job _reschedule_current_execution created must not survive: the
    # row is still logically soft-deleted after rollback.
    assert removed_ids == [11]
    callback.answer.assert_awaited()


async def test_recovery_done_all_commit_failure_never_touches_the_scheduler() -> None:
    now = datetime.now(timezone.utc)
    tasks = [
        SimpleNamespace(
            id=i, user_id=1, execution_time=now.replace(tzinfo=None) - timedelta(hours=1),
            is_recurring=False, rrule_string=None, is_nagging=False,
            is_habit=False, is_fluid_habit=False, habit_active_due_at=None,
            completed_for_execution_time=None,
        )
        for i in (1, 2)
    ]
    session = SimpleNamespace(commit=_failing_commit(), rollback=AsyncMock(), refresh=AsyncMock())
    reminder_dao = SimpleNamespace(
        get_overdue_pending_tasks=AsyncMock(return_value=tasks),
        mark_done=AsyncMock(),
        session=session,
    )
    habit_event_dao = SimpleNamespace()

    scheduled_calls: list = []
    removed_ids: list[int] = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append((rid, dt)),
        remove_reminder_job=lambda rid: removed_ids.append(rid),
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())

    await callback_recovery_done_all(callback, reminder_dao, habit_event_dao, scheduler_service, user, l10n)

    # 3.5: DB mutations happen first, and the scheduler is only touched
    # AFTER a successful commit — on a commit failure here, nothing has
    # been sent to the scheduler yet, so there's nothing to restore or
    # remove either.
    assert scheduled_calls == []
    assert removed_ids == []
    callback.answer.assert_awaited()


async def test_recovery_snooze_all_commit_failure_never_touches_the_scheduler() -> None:
    now = datetime.now(timezone.utc)
    tasks = [
        SimpleNamespace(
            id=i, user_id=1, execution_time=now.replace(tzinfo=None) - timedelta(hours=1),
            is_recurring=False, is_nagging=False,
            is_habit=False, is_fluid_habit=False,
            completed_for_execution_time=None, last_nag_chat_id=None, last_nag_message_id=None,
        )
        for i in (5, 6)
    ]
    session = SimpleNamespace(commit=_failing_commit(), rollback=AsyncMock(), refresh=AsyncMock())
    reminder_dao = SimpleNamespace(
        get_overdue_pending_tasks=AsyncMock(return_value=tasks),
        session=session,
    )

    scheduled_calls: list = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append((rid, dt)),
        remove_reminder_job=lambda rid: scheduled_calls.append((rid, "REMOVED")),
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())

    await callback_recovery_snooze_all(callback, reminder_dao, scheduler_service, user, l10n)

    # 3.5: same reasoning as callback_recovery_done_all's test above — the
    # scheduler is only touched after a successful commit.
    assert scheduled_calls == []
    callback.answer.assert_awaited()
