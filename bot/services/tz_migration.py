"""Habit timezone migration — offered when a user changes their timezone in
Settings, so existing fixed-time habits keep firing at the same wall-clock
time of day instead of silently drifting.

`execution_time` is stored as naive UTC. Changing `user.timezone` alone
doesn't touch it: a habit anchored at "09:00" under the old zone keeps that
same UTC instant, which reads as a different local time under the new zone
(see `bot/utils/time_ext.next_occurrence_utc`, which re-derives the rrule's
local anchor from `execution_time` using whatever timezone is current at the
time it's called). Migrating means re-localizing the *old* wall-clock time
under the *new* zone, so "09:00" stays "09:00".
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

import pytz

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Reminder
from bot.services.scheduler import SchedulerService
from bot.utils.time_ext import to_utc_aware, to_utc_naive


@dataclass(frozen=True)
class HabitTzMigrationItem:
    reminder_id: int
    text: str
    old_local_hhmm: str  # wall-clock time under the OLD zone (= new time if migrated)
    drift_local_hhmm: str  # what that same instant reads as under the NEW zone if left alone
    new_execution_time_utc: datetime  # naive UTC — migrated execution_time
    delta: timedelta  # new_execution_time_utc - old execution_time


def _safe_tz(tz_str: str) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(tz_str)
    except Exception:
        return pytz.UTC


def _local_hhmm(dt_utc_naive: datetime, tz: pytz.BaseTzInfo) -> str:
    return to_utc_aware(dt_utc_naive).astimezone(tz).strftime("%H:%M")


def migratable_habits(habits: Sequence[Reminder], old_tz_str: str) -> list[Reminder]:
    """Filter `get_active_habits()` rows down to ones with a meaningful,
    user-facing scheduled time.

    Fixed-time habits always qualify. Fluid habits only qualify when they
    have a live plan for "today" (under the OLD zone) — otherwise
    `execution_time` is just the 5-years-out placeholder set at creation
    and there's nothing meaningful to migrate.
    """
    old_tz = _safe_tz(old_tz_str)
    today_str = datetime.now(old_tz).date().isoformat()
    out = []
    for habit in habits:
        if habit.is_fluid_habit:
            if habit.fluid_planned_date == today_str and habit.fluid_planned_time:
                out.append(habit)
        else:
            out.append(habit)
    return out


def build_migration_plan(
    habits: Sequence[Reminder], old_tz_str: str, new_tz_str: str
) -> list[HabitTzMigrationItem]:
    """Compute, for each habit, the execution_time that keeps its
    wall-clock time-of-day stable across the zone change, plus what would
    happen if the user leaves it unmigrated."""
    old_tz = _safe_tz(old_tz_str)
    new_tz = _safe_tz(new_tz_str)
    items: list[HabitTzMigrationItem] = []
    for habit in habits:
        old_local_naive = to_utc_aware(habit.execution_time).astimezone(old_tz).replace(tzinfo=None)
        new_execution_time_utc = to_utc_naive(new_tz.localize(old_local_naive))
        items.append(
            HabitTzMigrationItem(
                reminder_id=habit.id,
                text=habit.reminder_text,
                old_local_hhmm=old_local_naive.strftime("%H:%M"),
                drift_local_hhmm=_local_hhmm(habit.execution_time, new_tz),
                new_execution_time_utc=new_execution_time_utc,
                delta=new_execution_time_utc - habit.execution_time,
            )
        )
    return items


async def apply_migration(
    items: Sequence[HabitTzMigrationItem],
    selected_ids: set[int],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> tuple[list[HabitTzMigrationItem], list[HabitTzMigrationItem]]:
    """Apply the chosen subset of a migration plan.

    Returns (migrated, kept_as_is) — both lists are subsets of *items*, in
    the same order, so a caller can render a summary from either.
    """
    migrated: list[HabitTzMigrationItem] = []
    kept: list[HabitTzMigrationItem] = []
    for item in items:
        if item.reminder_id not in selected_ids:
            kept.append(item)
            continue

        reminder: Optional[Reminder] = await reminder_dao.get_by_id(item.reminder_id)
        if reminder is None or reminder.pending_delete_at is not None:
            # Deleted/soft-deleted mid-flow — nothing to migrate any more.
            continue

        delta = item.delta
        prev_execution_time = reminder.execution_time
        prev_completed_for = reminder.completed_for_execution_time
        prev_active_due_at = reminder.habit_active_due_at

        reminder.execution_time = item.new_execution_time_utc
        if reminder.completed_for_execution_time is not None:
            reminder.completed_for_execution_time += delta
        if reminder.habit_active_due_at is not None:
            reminder.habit_active_due_at += delta

        try:
            scheduler_service.schedule_reminder(
                reminder.id,
                to_utc_aware(item.new_execution_time_utc),
                is_nagging=reminder.is_nagging,
            )
        except Exception:
            # Scheduler rejected the change — leave this row exactly as it
            # was rather than a half-migrated state, and don't touch the
            # rest of the batch (a shared session rollback here would wipe
            # out items already migrated earlier in this same loop).
            reminder.execution_time = prev_execution_time
            reminder.completed_for_execution_time = prev_completed_for
            reminder.habit_active_due_at = prev_active_due_at
            kept.append(item)
            continue

        migrated.append(item)

    return migrated, kept
