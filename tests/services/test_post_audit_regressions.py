"""Regressions found in review of the 22.09.26 audit fixes (PR #10).

1. A deliberate time edit on a recurring reminder must move its series
   anchor (rrule_dtstart) too — otherwise the next fire rebuilds the OLD
   time-of-day and silently undoes the edit.
2. Migrating a habit to a new timezone must re-localize the anchor the same
   way as execution_time — otherwise the migration is undone on next fire.
3. The hourly reconcile must not schedule a catch-up for a reminder that is
   being sent right now (its date job is already gone from the jobstore,
   last_fired_at not yet committed).
4/5. Parser: explicit period-of-day phrases keep a no-confirmation score;
   "сегодня" no longer slips past the date-only check; "в 2 дня" is 14:00.
6. A redelivered successful_payment must not poison the request session.
7. A stuck stop_polling() must not eat the whole shutdown budget.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot.services.scheduler as scheduler_module
from bot.utils.time_ext import next_occurrence_utc


def _local_hhmm(dt_utc_naive: datetime, tz: str) -> str:
    return pytz.UTC.localize(dt_utc_naive).astimezone(pytz.timezone(tz)).strftime("%H:%M")


# ---------------------------------------------------------------------------
# 1. Time edit re-anchors the series
# ---------------------------------------------------------------------------

async def _run_edit(*, is_snooze_mode: bool, new_time: datetime):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.handlers.reminders_shared import _save_and_show_edit
    from bot.lexicon import get_l10n

    old_anchor = datetime(2026, 9, 1, 6, 0)  # 09:00 MSK
    reminder = SimpleNamespace(
        id=1,
        reminder_text="Standup",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) + timedelta(hours=20),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        rrule_dtstart=old_anchor,
        is_nagging=False,
        nagging_max_repeats=3,
        snooze_count=0,
        tags=None,
        priority=None,
    )
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.update_data(
        text="Standup",
        execution_time=new_time.replace(tzinfo=timezone.utc).isoformat(),
        edit_reminder_id=1,
        is_snooze_mode=is_snooze_mode,
    )
    user = SimpleNamespace(id=1, timezone="Europe/Moscow", show_utc_offset=False)
    message = SimpleNamespace(answer=AsyncMock())
    await _save_and_show_edit(message, state, get_l10n("ru"), user, reminder_dao, scheduler_service)
    return reminder, old_anchor


async def test_time_edit_on_recurring_reminder_survives_the_next_fire() -> None:
    today = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    new_time = (today + timedelta(days=1)).replace(hour=15, minute=0)  # 18:00 MSK tomorrow
    reminder, _ = await _run_edit(is_snooze_mode=False, new_time=new_time)

    assert reminder.rrule_dtstart == new_time
    # What _execute_reminder computes right after that edited fire:
    nxt = next_occurrence_utc(reminder.rrule_string, reminder.rrule_dtstart, "Europe/Moscow", new_time)
    assert _local_hhmm(nxt, "Europe/Moscow") == "18:00"


async def test_custom_snooze_on_recurring_reminder_keeps_the_anchor() -> None:
    today = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    reminder, old_anchor = await _run_edit(
        is_snooze_mode=True, new_time=today + timedelta(hours=2)
    )
    assert reminder.rrule_dtstart == old_anchor


# ---------------------------------------------------------------------------
# 2. Timezone migration re-anchors the series
# ---------------------------------------------------------------------------

async def test_tz_migration_moves_the_anchor_and_survives_the_next_fire() -> None:
    from bot.services.tz_migration import apply_migration, build_migration_plan

    base = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0) + timedelta(hours=2)
    anchor = base - timedelta(days=30)
    habit = SimpleNamespace(
        id=1, reminder_text="Zaryadka", execution_time=base, rrule_dtstart=anchor,
        is_fluid_habit=False, fluid_planned_date=None, fluid_planned_time=None,
        completed_for_execution_time=None, habit_active_due_at=None, is_nagging=False,
        pending_delete_at=None, is_recurring=True, rrule_string="FREQ=DAILY",
    )
    old_local = _local_hhmm(anchor, "Europe/Moscow")

    items = build_migration_plan([habit], "Europe/Moscow", "Asia/Tokyo")
    reminder_dao = SimpleNamespace(get_by_id=AsyncMock(return_value=habit))
    migrated, _ = await apply_migration(
        items, {1}, reminder_dao, SimpleNamespace(schedule_reminder=MagicMock()), "Asia/Tokyo"
    )

    assert [i.reminder_id for i in migrated] == [1]
    assert _local_hhmm(habit.rrule_dtstart, "Asia/Tokyo") == old_local
    nxt = next_occurrence_utc(habit.rrule_string, habit.rrule_dtstart, "Asia/Tokyo", habit.execution_time)
    assert _local_hhmm(nxt, "Asia/Tokyo") == old_local


async def test_tz_migration_rollback_restores_the_anchor() -> None:
    from bot.services.tz_migration import apply_migration, build_migration_plan

    base = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0) + timedelta(hours=2)
    anchor = base - timedelta(days=30)
    habit = SimpleNamespace(
        id=1, reminder_text="Zaryadka", execution_time=base, rrule_dtstart=anchor,
        is_fluid_habit=False, fluid_planned_date=None, fluid_planned_time=None,
        completed_for_execution_time=None, habit_active_due_at=None, is_nagging=False,
        pending_delete_at=None, is_recurring=True, rrule_string="FREQ=DAILY",
    )
    items = build_migration_plan([habit], "Europe/Moscow", "Asia/Tokyo")
    failing_scheduler = SimpleNamespace(schedule_reminder=MagicMock(side_effect=RuntimeError("jobstore down")))
    await apply_migration(
        items, {1}, SimpleNamespace(get_by_id=AsyncMock(return_value=habit)), failing_scheduler, "Asia/Tokyo"
    )
    assert habit.rrule_dtstart == anchor
    assert habit.execution_time == base


# ---------------------------------------------------------------------------
# 3. Reconcile skips a reminder that is being sent right now
# ---------------------------------------------------------------------------

class _ReconcileSession:
    def __init__(self, reminders):
        rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: reminders))
        tz_rows = SimpleNamespace(all=lambda: [])
        self._responses = [rows, tz_rows]

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_reconcile_skips_a_reminder_that_is_in_flight(monkeypatch) -> None:
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
    reminder = SimpleNamespace(
        id=9, user_id=1, status="pending", is_fluid_habit=False, is_recurring=False,
        rrule_string=None, is_nagging=False, last_fired_at=None, forbidden_strikes=0,
        pending_delete_at=None, execution_time=past,
    )
    scheduler = AsyncIOScheduler()
    service = scheduler_module.SchedulerService(
        scheduler, bot=SimpleNamespace(), session_pool=lambda: _ReconcileSession([reminder])
    )
    monkeypatch.setattr(scheduler_module, "_in_flight_reminder_ids", {9: 1})

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("9") is None  # no duplicate catch-up for a send in progress


async def test_in_flight_reminder_ids_are_tracked_and_released(monkeypatch) -> None:
    import bot.context as context_module

    seen_during_run = {}

    async def _execute(reminder_id, is_nagging_execution=False):
        seen_during_run.update(scheduler_module._in_flight_reminder_ids)

    fake_scheduler = SimpleNamespace(_execute_reminder=_execute, _crash_retry_counts={})
    monkeypatch.setattr(context_module, "_context", SimpleNamespace(scheduler=fake_scheduler))
    monkeypatch.setattr(scheduler_module, "_in_flight_reminder_ids", {})

    await scheduler_module.execute_reminder_job(5)

    assert seen_during_run == {5: 1}
    assert scheduler_module._in_flight_reminder_ids == {}


# ---------------------------------------------------------------------------
# 4/5. Parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,hour",
    [
        ("купить хлеб в 7 утра", 7),
        ("в 5 вечера позвонить маме", 17),
        ("в 12 ночи спать", 0),
        ("в 2 дня обед", 14),
        ("в 12 дня обед", 12),
        ("в 3 часа дня встреча", 15),
    ],
)
def test_explicit_period_phrase_is_correct_and_needs_no_confirmation(text, hour) -> None:
    from bot.services.parser import InputParser

    result = InputParser()._parse_sync(text, "Europe/Moscow")
    assert result.parsed_datetime is not None
    assert (result.parsed_datetime.hour, result.parsed_datetime.minute) == (hour, 0)
    assert result.confidence >= 0.7


@pytest.mark.parametrize("text", ["позвонить сегодня", "call mom today"])
def test_today_without_a_time_goes_through_confirmation(text) -> None:
    from bot.services.parser import InputParser

    result = InputParser()._parse_sync(text, "Europe/Moscow")
    assert result.confidence < 0.7


def test_duration_in_days_is_not_mistaken_for_a_period_of_day() -> None:
    from bot.services.parser import InputParser

    before = datetime.now(pytz.timezone("Europe/Moscow"))
    result = InputParser()._parse_sync("через 3 дня отчёт", "Europe/Moscow")
    assert result.parsed_datetime is not None
    assert timedelta(days=3) - timedelta(minutes=5) <= result.parsed_datetime - before <= timedelta(days=3, minutes=5)
    assert result.confidence >= 0.7


# ---------------------------------------------------------------------------
# 6. Redelivered successful_payment
# ---------------------------------------------------------------------------

async def test_redelivered_payment_keeps_the_session_usable(tmp_path) -> None:
    from bot.database.dao.payment import PaymentDAO
    from bot.database.dao.user import UserDAO
    from bot.database.engine import create_engine, create_session_maker, dispose_engine, init_db
    from bot.handlers.donate import process_successful_payment
    from bot.lexicon.ru import RU

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'pay.db'}")
    try:
        await init_db(engine)
        pool = create_session_maker(engine)
        async with pool() as session:
            await UserDAO(session).get_or_create(7)
            await session.commit()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            answer=AsyncMock(),
            successful_payment=SimpleNamespace(
                total_amount=50, currency="XTR", telegram_payment_charge_id="c1", invoice_payload="donate:50"
            ),
        )
        for _ in range(2):  # Telegram redelivers the same update
            async with pool() as session:  # what DatabaseMiddleware wraps the handler in
                await process_successful_payment(message, RU, PaymentDAO(session))
                await session.commit()  # used to raise PendingRollbackError on delivery #2

        async with pool() as session:
            payments = await PaymentDAO(session).get_recent_for_user(7)
        assert len(payments) == 1
        assert message.answer.await_count == 2
    finally:
        await dispose_engine(engine)


# ---------------------------------------------------------------------------
# 7. Stuck stop_polling()
# ---------------------------------------------------------------------------

async def test_stuck_stop_polling_falls_back_to_cancel(monkeypatch) -> None:
    import bot.__main__ as main_module

    monkeypatch.setattr(main_module, "POLLING_STOP_TIMEOUT_SECONDS", 0.05)
    stop_event = asyncio.Event()
    cancelled = False

    async def _polling_forever():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    class _StuckDispatcher:
        async def stop_polling(self):
            await asyncio.Event().wait()  # a handler that never finishes

    async def _trigger():
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.ensure_future(_trigger())
    await asyncio.wait_for(
        main_module._run_until_stopped(_polling_forever(), stop_event, _StuckDispatcher()), timeout=2
    )
    assert cancelled is True
