from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.services.tz_migration import (
    apply_migration,
    build_migration_plan,
    migratable_habits,
)


def _habit(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1,
        reminder_text="Zaryadka",
        execution_time=datetime(2026, 1, 15, 6, 0),  # 09:00 Europe/Moscow / 01:00 America/New_York (Jan, EST)
        is_fluid_habit=False,
        fluid_planned_date=None,
        fluid_planned_time=None,
        completed_for_execution_time=None,
        habit_active_due_at=None,
        is_nagging=False,
        pending_delete_at=None,
        # Matches how habits.py actually creates a fixed-time habit
        # (is_recurring=True, rrule_string="FREQ=DAILY") — apply_migration
        # (1.4) needs both to decide whether a past-landing migration can
        # be advanced to the next real occurrence.
        is_recurring=True,
        rrule_string="FREQ=DAILY",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_migration_plan_preserves_wall_clock_time() -> None:
    habit = _habit()
    [item] = build_migration_plan([habit], "Europe/Moscow", "America/New_York")

    assert item.old_local_hhmm == "09:00"
    # Left unmigrated, the same UTC instant reads as 01:00 in New York (Jan, EST = UTC-5).
    assert item.drift_local_hhmm == "01:00"
    # Migrated, the new UTC value should read back as 09:00 under the new zone.
    import pytz

    new_ny_local = pytz.UTC.localize(item.new_execution_time_utc).astimezone(pytz.timezone("America/New_York"))
    assert new_ny_local.strftime("%H:%M") == "09:00"
    assert item.delta == item.new_execution_time_utc - habit.execution_time
    assert item.delta != timedelta(0)


def test_build_migration_plan_noop_when_zone_unchanged() -> None:
    habit = _habit()
    [item] = build_migration_plan([habit], "Europe/Moscow", "Europe/Moscow")
    assert item.new_execution_time_utc == habit.execution_time
    assert item.delta == timedelta(0)
    assert item.old_local_hhmm == item.drift_local_hhmm


def test_migratable_habits_includes_fixed_time_habits() -> None:
    fixed = _habit(id=1, is_fluid_habit=False)
    assert migratable_habits([fixed], "Europe/Moscow") == [fixed]


def test_migratable_habits_excludes_fluid_without_todays_plan() -> None:
    fluid_no_plan = _habit(id=2, is_fluid_habit=True, fluid_planned_date=None, fluid_planned_time=None)
    fluid_stale_plan = _habit(id=3, is_fluid_habit=True, fluid_planned_date="2020-01-01", fluid_planned_time="08:00")
    assert migratable_habits([fluid_no_plan, fluid_stale_plan], "Europe/Moscow") == []


def test_migratable_habits_includes_fluid_with_todays_plan() -> None:
    import pytz

    today_str = datetime.now(pytz.timezone("Europe/Moscow")).date().isoformat()
    fluid_today = _habit(id=4, is_fluid_habit=True, fluid_planned_date=today_str, fluid_planned_time="08:00")
    assert migratable_habits([fluid_today], "Europe/Moscow") == [fluid_today]


async def test_apply_migration_shifts_selected_and_leaves_rest_untouched() -> None:
    # Anchored relative to "now" (not a hardcoded calendar date) so this
    # stays a "migrated time is still in the future" case no matter when
    # the suite runs — Moscow (UTC+3) -> New York (EST, UTC-5) always
    # shifts the UTC instant *later* by 8h for the same wall-clock time, so
    # +2h here can never accidentally land in the past (see 1.4's new
    # past-guard in apply_migration, exercised separately below).
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    base_time = now_utc_naive + timedelta(hours=2)
    migrate_me = _habit(id=1, execution_time=base_time, completed_for_execution_time=base_time, habit_active_due_at=base_time)
    leave_me = _habit(id=2, execution_time=base_time + timedelta(hours=1))

    items = build_migration_plan([migrate_me, leave_me], "Europe/Moscow", "America/New_York")
    by_id = {1: migrate_me, 2: leave_me}

    reminder_dao = SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda rid: by_id[rid]))
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())

    migrated, kept = await apply_migration(items, {1}, reminder_dao, scheduler_service, "America/New_York")

    assert [i.reminder_id for i in migrated] == [1]
    assert [i.reminder_id for i in kept] == [2]

    # Selected habit: execution_time (and the fields tied to it) moved by the same delta.
    delta = migrate_me.execution_time - base_time
    assert delta > timedelta(0)
    assert migrate_me.completed_for_execution_time == base_time + delta
    assert migrate_me.habit_active_due_at == base_time + delta
    scheduler_service.schedule_reminder.assert_called_once()
    assert scheduler_service.schedule_reminder.call_args.args[0] == 1

    # Unselected habit: left exactly as it was.
    assert leave_me.execution_time == base_time + timedelta(hours=1)


async def test_apply_migration_skips_soft_deleted_habit() -> None:
    deleted = _habit(id=5, pending_delete_at=datetime(2026, 1, 15, 6, 0))
    items = build_migration_plan([deleted], "Europe/Moscow", "America/New_York")
    reminder_dao = SimpleNamespace(get_by_id=AsyncMock(return_value=deleted))
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())

    migrated, kept = await apply_migration(items, {5}, reminder_dao, scheduler_service, "America/New_York")

    assert migrated == []
    assert kept == []  # neither migrated nor "kept as is" — it no longer exists to report on
    scheduler_service.schedule_reminder.assert_not_called()


# --- 1.4: a migration that would land in the past must not fire immediately ---


async def test_apply_migration_advances_recurring_habit_past_due_to_next_occurrence() -> None:
    """Old zone UTC, new zone UTC+12: re-localizing the same wall-clock time
    under a zone 12h further east moves the UTC instant 12h EARLIER. With
    the habit only 2h out, that lands the naive re-localized result 10h in
    the PAST. For a recurring fixed-time habit, apply_migration must
    advance to the next real daily occurrence instead of letting
    APScheduler's misfire_grace_time fire it immediately."""
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    habit = _habit(id=1, execution_time=now_utc_naive + timedelta(hours=2))

    items = build_migration_plan([habit], "UTC", "Etc/GMT-12")  # Etc/GMT-12 == UTC+12 (IANA sign reversed)
    assert items[0].new_execution_time_utc < now_utc_naive  # confirms the setup actually lands in the past

    reminder_dao = SimpleNamespace(get_by_id=AsyncMock(return_value=habit))
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())

    before_call = datetime.now(timezone.utc).replace(tzinfo=None)
    migrated, kept = await apply_migration(items, {1}, reminder_dao, scheduler_service, "Etc/GMT-12")

    assert [i.reminder_id for i in migrated] == [1]
    assert kept == []
    # The mutated reminder's execution_time is the advanced (future) slot,
    # never the raw re-localized (past) one.
    assert habit.execution_time > before_call
    scheduler_service.schedule_reminder.assert_called_once()
    scheduled_run_date = scheduler_service.schedule_reminder.call_args.args[1]
    assert scheduled_run_date > before_call.replace(tzinfo=timezone.utc)


async def test_apply_migration_leaves_fluid_habit_unmigrated_when_result_would_land_in_the_past() -> None:
    """A fluid habit has no rrule to advance along — migrating it into the
    past has no safe "next occurrence" to fall back to, so it must be left
    exactly as-is (reported as "kept"), not fired immediately."""
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    original_execution_time = now_utc_naive + timedelta(hours=2)
    habit = _habit(
        id=1,
        execution_time=original_execution_time,
        is_fluid_habit=True,
        is_recurring=True,
        rrule_string=None,  # matches how fluid habits are actually created
        fluid_planned_date=datetime.now(timezone.utc).date().isoformat(),
        fluid_planned_time="12:00",
    )

    items = build_migration_plan([habit], "UTC", "Etc/GMT-12")
    assert items[0].new_execution_time_utc < now_utc_naive

    reminder_dao = SimpleNamespace(get_by_id=AsyncMock(return_value=habit))
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())

    migrated, kept = await apply_migration(items, {1}, reminder_dao, scheduler_service, "Etc/GMT-12")

    assert migrated == []
    assert [i.reminder_id for i in kept] == [1]
    assert habit.execution_time == original_execution_time  # untouched
    scheduler_service.schedule_reminder.assert_not_called()
