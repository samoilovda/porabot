"""1.3/2.2: callback_snooze_act, callback_done_undo, and
callback_done_skip_next all reschedule an APScheduler job BEFORE the
DB write that describes it is durable. If DatabaseMiddleware's implicit
commit (which only runs once the handler returns) fails, the user must
not see a success message while the job now points somewhere the DB row
doesn't back up. Each handler must commit explicitly first and, on
failure, restore a job consistent with the actual (rolled-back) DB state
rather than leaving the mismatched one in place.

Also covers callback_snooze_act's None-safe fallback for
callback.message.text (a media message has no .text, only .caption).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.reminders_completion import callback_done_skip_next, callback_done_undo
from bot.handlers.reminders_snooze import callback_snooze_act
from bot.lexicon import get_l10n


def _failing_commit():
    async def _fail():
        raise RuntimeError("database is locked")
    return _fail


async def test_snooze_commit_failure_restores_prior_job_and_survives_missing_text() -> None:
    old_time = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) + timedelta(hours=1)
    reminder = SimpleNamespace(
        id=5,
        user_id=1,
        execution_time=old_time,
        is_recurring=False,
        is_habit=False,
        is_fluid_habit=False,
        is_nagging=False,
        last_nag_chat_id=None,
        last_nag_message_id=None,
    )
    session = SimpleNamespace(
        commit=_failing_commit(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),  # leaves `reminder` object as-is — its execution_time is unchanged
    )
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)

    scheduled_calls: list[datetime] = []
    removed_ids: list[int] = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append(dt),
        remove_nagging_job=lambda rid: None,
        remove_reminder_job=lambda rid: removed_ids.append(rid),
    )

    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    # callback.message.text is None — a media message, only .caption is set.
    callback = SimpleNamespace(
        data="snooze_act_5_1h",
        message=SimpleNamespace(text=None, caption="original caption"),
        answer=AsyncMock(),
    )
    state = SimpleNamespace()

    await callback_snooze_act(callback, reminder_dao, scheduler_service, state, user, l10n)

    # Scheduled once for the chosen snooze time, once more to restore the
    # prior (rolled-back) execution_time — never left orphaned.
    assert len(scheduled_calls) == 2
    assert removed_ids == []
    assert abs((scheduled_calls[-1].replace(tzinfo=None) - old_time).total_seconds()) < 5
    callback.answer.assert_awaited()
    # Did not crash on message.text being None.


async def test_done_undo_commit_failure_restores_prior_job() -> None:
    old_time = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) + timedelta(hours=1)
    reminder = SimpleNamespace(
        id=7,
        user_id=1,
        execution_time=old_time,
        is_recurring=False,
        is_habit=False,
        is_fluid_habit=False,
        is_nagging=False,
        habit_active_due_at=None,
        status="completed",
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        completed_for_execution_time=old_time,
        last_completion_note=None,
        last_nag_chat_id=None,
        last_nag_message_id=None,
    )
    session = SimpleNamespace(
        commit=_failing_commit(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)
    habit_event_dao = SimpleNamespace()

    scheduled_calls: list[datetime] = []
    removed_ids: list[int] = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append(dt),
        remove_reminder_job=lambda rid: removed_ids.append(rid),
    )

    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(data="done_undo_7", answer=AsyncMock())

    await callback_done_undo(callback, reminder_dao, habit_event_dao, scheduler_service, user, l10n)

    assert len(scheduled_calls) == 2
    assert removed_ids == []
    callback.answer.assert_awaited()


async def test_done_skip_next_commit_failure_restores_prior_job() -> None:
    old_time = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) + timedelta(hours=1)
    reminder = SimpleNamespace(
        id=9,
        user_id=1,
        execution_time=old_time,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_nagging=False,
        completed_for_execution_time=None,
        last_nag_chat_id=None,
        last_nag_message_id=None,
    )
    session = SimpleNamespace(
        commit=_failing_commit(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)

    scheduled_calls: list[datetime] = []
    removed_ids: list[int] = []
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append(dt),
        remove_nagging_job=lambda rid: None,
        remove_reminder_job=lambda rid: removed_ids.append(rid),
    )

    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")
    callback = SimpleNamespace(data="done_skip_next_9", answer=AsyncMock())

    await callback_done_skip_next(callback, reminder_dao, scheduler_service, user, l10n)

    assert len(scheduled_calls) == 2
    assert removed_ids == []
    callback.answer.assert_awaited()
