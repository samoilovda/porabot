"""Repeatedly snoozing one reminder through the day used to leave a fresh
"Postponed until ..." message behind every single time (each re-fire sends
a brand-new message, and callback_snooze_act only ever edited it in place,
never deleted it). callback_snooze_act now lets the first snooze of a
cycle stay visible as before, but deletes the message on the 2nd+ snooze
instead of piling on — while reminder.snooze_count keeps the running total
regardless. ReminderDAO.mark_done/mark_habit_not_today/
mark_fluid_habit_done_today reset that counter once the cycle actually
closes.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.reminder import ReminderDAO
from bot.handlers.reminders_snooze import callback_snooze_act
from bot.lexicon import get_l10n


def _make_reminder(**overrides) -> SimpleNamespace:
    base = dict(
        id=5,
        user_id=1,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        is_recurring=False,
        is_habit=False,
        is_fluid_habit=False,
        is_nagging=False,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        snooze_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_env(reminder):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None,
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    return reminder_dao, scheduler_service, user


async def test_first_snooze_edits_message_in_place() -> None:
    reminder = _make_reminder()
    reminder_dao, scheduler_service, user = _make_env(reminder)
    callback = SimpleNamespace(
        data="snooze_act_5_1h",
        message=SimpleNamespace(text="Workout", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_snooze_act(callback, reminder_dao, scheduler_service, SimpleNamespace(), user, get_l10n("en"))

    assert reminder.snooze_count == 1
    callback.message.edit_text.assert_awaited_once()
    callback.message.delete.assert_not_called()


async def test_second_snooze_deletes_message_but_keeps_count() -> None:
    reminder = _make_reminder(snooze_count=1)
    reminder_dao, scheduler_service, user = _make_env(reminder)
    callback = SimpleNamespace(
        data="snooze_act_5_1h",
        message=SimpleNamespace(text="Workout", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_snooze_act(callback, reminder_dao, scheduler_service, SimpleNamespace(), user, get_l10n("en"))

    assert reminder.snooze_count == 2
    callback.message.delete.assert_awaited_once()
    callback.message.edit_text.assert_not_called()
    callback.answer.assert_awaited()


async def test_third_snooze_still_deletes_and_keeps_incrementing() -> None:
    reminder = _make_reminder(snooze_count=2)
    reminder_dao, scheduler_service, user = _make_env(reminder)
    callback = SimpleNamespace(
        data="snooze_act_5_30m",
        message=SimpleNamespace(text="Workout", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_snooze_act(callback, reminder_dao, scheduler_service, SimpleNamespace(), user, get_l10n("en"))

    assert reminder.snooze_count == 3
    callback.message.delete.assert_awaited_once()


async def test_mark_done_resets_snooze_count() -> None:
    reminder = _make_reminder(
        snooze_count=4,
        status="pending",
        completed_at=None,
        completed_for_execution_time=None,
        last_completion_note=None,
    )
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]

    await dao.mark_done(reminder.id)

    assert reminder.snooze_count == 0


async def test_mark_habit_not_today_resets_snooze_count() -> None:
    reminder = _make_reminder(
        snooze_count=3,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        completed_for_execution_time=None,
    )
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]

    await dao.mark_habit_not_today(reminder.id)

    assert reminder.snooze_count == 0


async def test_mark_fluid_habit_done_today_resets_snooze_count() -> None:
    reminder = _make_reminder(
        snooze_count=2,
        is_fluid_habit=True,
        fluid_last_completed_date=None,
        fluid_streak_current=0,
        fluid_streak_best=0,
    )
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]

    result = await dao.mark_fluid_habit_done_today(reminder.id, "UTC")

    assert result is True
    assert reminder.snooze_count == 0
