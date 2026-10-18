"""One daily completion transaction for chat lists, with an exact undo snapshot."""

import hashlib
import json
from datetime import datetime, timedelta

from bot.database.dao.habit_event import cycle_key_for_fixed, cycle_key_for_fluid
from bot.database.models import UiContext
from bot.services.ui_context import now_utc

_FIELDS = (
    "completed_at",
    "completed_for_execution_time",
    "habit_streak_current",
    "habit_streak_best",
    "habit_last_completed_due_at",
    "habit_undo_pending",
    "fluid_streak_current",
    "fluid_streak_best",
    "fluid_last_completed_date",
    "snooze_count",
)
_DATES = {"completed_at", "completed_for_execution_time", "habit_last_completed_due_at"}


async def set_daily_completion(dao, events, task, user, cycle, *, undo=False):
    """Caller validates ownership, pause, current day and the due identifier."""
    from sqlalchemy import update

    from bot.database.models import Reminder
    from bot.services.habit_pause import is_paused

    # Serialize changes to a habit before reading the undo snapshot or streak.
    locked = await dao.session.execute(
        update(Reminder).where(Reminder.id == task.id, Reminder.user_id == user.id).values(id=Reminder.id)
    )
    if locked.rowcount != 1:
        await dao.session.commit()
        return False
    await dao.session.refresh(task)
    if is_paused(task):
        await dao.session.commit()
        return False
    if not task.is_habit and not task.is_fluid_habit:
        await dao.session.commit()
        return False
    fluid = task.is_fluid_habit
    key = cycle_key_for_fluid(cycle) if fluid else cycle_key_for_fixed(cycle)
    token = completion_token(user.id, task.id, key)
    snapshot = await dao.session.get(UiContext, token)
    if undo:
        if snapshot is None or snapshot.expires_at <= now_utc() or snapshot.result != "done":
            await dao.session.commit()
            return False
        before = json.loads(snapshot.payload_json)
        for field in _FIELDS:
            value = before[field]
            setattr(task, field, datetime.fromisoformat(value) if field in _DATES and value is not None else value)
        await events.delete_for_cycle(task.id, key)
        snapshot.result = "undone"
    else:
        if await events.has_event_for_cycle(task.id, key):
            await dao.session.commit()
            return False
        if snapshot is None:
            before = {
                field: (
                    getattr(task, field).isoformat()
                    if field in _DATES and getattr(task, field)
                    else getattr(task, field)
                )
                for field in _FIELDS
            }
            snapshot = UiContext(
                token=token,
                user_id=user.id,
                kind="habitundo",
                payload_json=json.dumps(before),
                expires_at=now_utc() + timedelta(days=2),
            )
            dao.session.add(snapshot)
        inserted = await events.record(
            reminder=task,
            user_tz=user.timezone,
            outcome="done",
            source="daily",
            **({"local_date": cycle} if fluid else {"due_at_utc_naive": cycle}),
        )
        if not inserted:
            await dao.session.commit()
            return False
        if fluid:
            await dao.mark_fluid_habit_done_today(task.id, user.timezone)
        else:
            await dao.apply_habit_streak_completion(task.id, due_at_utc_naive=cycle, completed_at_utc_naive=now_utc())
            await dao.mark_done(task.id)
            if task.execution_time == cycle:
                task.completed_for_execution_time = cycle
        snapshot.result = "done"
    await dao.session.commit()
    return True


def completion_token(user_id, reminder_id, cycle_key):
    return hashlib.sha256(f"{user_id}:{reminder_id}:{cycle_key}".encode()).hexdigest()[:16]
