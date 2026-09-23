"""docs/audits/2026-09-22-audit.md#a-02 / #a-03 — the RRULE series must be
anchored on a fixed Reminder.rrule_dtstart, never on the ever-advancing
execution_time. Before this column existed, every next_occurrence_utc call
used execution_time as dtstart — which moves forward on every fire (and, for
a plain recurring task, used to move on every snooze too) — so a "COUNT=3"
repeat's count restarted from whichever cycle happened to be current instead
of the series' true start, and effectively never stopped; a snoozed
recurring task drifted its daily time forward by the snooze amount, forever.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.handlers.reminders_shared import _apply_repeat_change, _rrule_base_key
from bot.handlers.reminders_snooze import callback_snooze_act
from bot.lexicon import get_l10n
from bot.services.scheduler import SchedulerService


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """One-shot fake session for a single _execute_reminder() call — same
    shape as test_habit_snooze_refire_streak_cycle.py's."""

    def __init__(self, reminder, user):
        self._responses = [_FakeResult(reminder), _FakeResult(user)]
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_reminder(**overrides) -> SimpleNamespace:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    base = dict(
        id=10,
        user_id=7,
        status="pending",
        reminder_text="Standup",
        execution_time=now,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        rrule_dtstart=now,
        is_habit=False,
        is_fluid_habit=False,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        forbidden_strikes=0,
        last_fired_at=None,
        pending_delete_at=None,
        send_retry_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_user() -> SimpleNamespace:
    return SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)


async def test_execute_reminder_anchors_recurrence_on_rrule_dtstart_not_execution_time() -> None:
    """A COUNT-limited series already exhausted relative to its true anchor
    (rrule_dtstart, 3 days ago) must not get a 4th job just because
    execution_time was independently dragged forward to "now" — the exact
    shape a series of snoozes used to leave behind before rrule_dtstart
    existed (execution_time drifting forward while dateutil kept recounting
    COUNT=3 from wherever execution_time currently was, never stopping).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    true_anchor = now - timedelta(days=3)
    reminder = _make_reminder(
        rrule_string="FREQ=DAILY;COUNT=3",
        rrule_dtstart=true_anchor,
        execution_time=now,  # drifted far from the true anchor
    )
    user = _make_user()
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: _FakeSession(reminder, user))

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    # If execution_time (not rrule_dtstart) had driven the calculation, a
    # brand-new 3-count series starting "now" would still have occurrences
    # left, and a job would have been scheduled.
    assert scheduler.get_job(str(reminder.id)) is None
    assert reminder.rrule_dtstart == true_anchor  # the anchor itself is never mutated by a fire


async def test_execute_reminder_never_mutates_rrule_dtstart_across_a_normal_fire() -> None:
    reminder = _make_reminder(rrule_string="FREQ=DAILY")
    anchor = reminder.rrule_dtstart
    user = _make_user()
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: _FakeSession(reminder, user))

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.rrule_dtstart == anchor
    assert reminder.execution_time != anchor  # execution_time DOES advance — only the anchor stays fixed
    assert scheduler.get_job(str(reminder.id)) is not None  # unlimited daily rule keeps a next job


# ---------------------------------------------------------------------------
# A-03: snoozing a PLAIN recurring (non-habit) reminder must not overwrite
# execution_time — that restriction used to only apply to habit-like
# reminders, so a plain "every day at 9" task drifted its own daily time
# forward by the snooze amount, permanently, every single time it was
# snoozed.
# ---------------------------------------------------------------------------

def _make_snooze_reminder(**overrides) -> SimpleNamespace:
    base = dict(
        id=5,
        user_id=1,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=False,
        is_fluid_habit=False,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
        is_nagging=False,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        snooze_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_snoozing_a_plain_recurring_task_does_not_shift_its_daily_time() -> None:
    reminder = _make_snooze_reminder()
    next_occurrence = reminder.execution_time  # scheduler already advanced this to tomorrow's slot
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None,
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    callback = SimpleNamespace(
        data="snooze_act_5_1h",
        message=SimpleNamespace(text="Standup", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_snooze_act(callback, reminder_dao, scheduler_service, SimpleNamespace(), user, get_l10n("en"))

    # execution_time (the series' next real occurrence) is untouched — only
    # the APScheduler job (mocked here) was told to fire the snooze early.
    assert reminder.execution_time == next_occurrence
    assert reminder.snooze_count == 1


async def test_snoozing_a_one_off_task_still_moves_its_execution_time() -> None:
    """Sanity check the widened guard didn't break the ordinary (non-
    recurring) snooze path, which must still move execution_time."""
    reminder = _make_snooze_reminder(is_recurring=False, rrule_string=None)
    original = reminder.execution_time
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), session=session)
    scheduler_service = SimpleNamespace(
        schedule_reminder=lambda *a, **k: None,
        remove_nagging_job=lambda rid: None,
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    callback = SimpleNamespace(
        data="snooze_act_5_1h",
        message=SimpleNamespace(text="Buy milk", caption=None, edit_text=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_snooze_act(callback, reminder_dao, scheduler_service, SimpleNamespace(), user, get_l10n("en"))

    assert reminder.execution_time != original


# ---------------------------------------------------------------------------
# A-02: the repeat builder must only reset the series anchor when the base
# pattern actually changes — an end-condition-only tweak (adding/changing
# COUNT=/UNTIL= on the SAME base rule) must keep counting from the series'
# true start, not restart it from "now" on every such tweak.
# ---------------------------------------------------------------------------

def test_rrule_base_key_ignores_count_and_until() -> None:
    assert _rrule_base_key("FREQ=DAILY;COUNT=3") == _rrule_base_key("FREQ=DAILY;UNTIL=20261231")
    assert _rrule_base_key("FREQ=DAILY") == _rrule_base_key("FREQ=DAILY;COUNT=3")
    assert _rrule_base_key("FREQ=DAILY") != _rrule_base_key("FREQ=WEEKLY")


async def test_apply_repeat_change_keeps_anchor_when_only_end_condition_changes() -> None:
    anchor = datetime(2026, 1, 1, 9, 0)
    reminder = SimpleNamespace(
        id=1,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        rrule_dtstart=anchor,
        execution_time=datetime(2026, 6, 1, 9, 0),  # far from the anchor by the time this tweak happens
        is_nagging=False,
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    reminder_dao = SimpleNamespace(
        session=SimpleNamespace(flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    )
    scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None, remove_reminder_job=lambda rid: None)

    ok = await _apply_repeat_change(
        reminder, user, scheduler_service, reminder_dao, True, "FREQ=DAILY;COUNT=5"
    )

    assert ok is True
    assert reminder.rrule_dtstart == anchor  # unchanged — same base pattern, only COUNT= added


async def test_apply_repeat_change_resets_anchor_when_base_pattern_changes() -> None:
    # A future execution_time — _reschedule_current_execution only advances
    # execution_time past "now" when it's already due, and this test cares
    # about what the anchor was set to at the moment of the change, not
    # about that separate advancing behavior (covered by the "keeps anchor"
    # test above, whose execution_time is deliberately in the past).
    original_execution_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
    reminder = SimpleNamespace(
        id=1,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        rrule_dtstart=datetime(2026, 1, 1, 9, 0),
        execution_time=original_execution_time,
        is_nagging=False,
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    reminder_dao = SimpleNamespace(
        session=SimpleNamespace(flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    )
    scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None, remove_reminder_job=lambda rid: None)

    ok = await _apply_repeat_change(
        reminder, user, scheduler_service, reminder_dao, True, "FREQ=WEEKLY"
    )

    assert ok is True
    assert reminder.rrule_dtstart == original_execution_time  # new base pattern -> fresh anchor
    assert reminder.execution_time == original_execution_time  # still in the future — untouched
