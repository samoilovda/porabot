"""Durable, expiring contexts keep long queries and batch IDs off Telegram buttons."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from bot.database.models import UiContext


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_context(session, user_id, kind, payload, *, lifetime=timedelta(hours=24)):
    await session.execute(delete(UiContext).where(UiContext.expires_at <= now_utc()))
    context = UiContext(
        token=secrets.token_hex(6),
        user_id=user_id,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False),
        expires_at=now_utc() + lifetime,
    )
    session.add(context)
    await session.commit()
    return context


async def get_context(session, token, user_id, kind=None):
    context = await session.scalar(
        select(UiContext).where(
            UiContext.token == token, UiContext.user_id == user_id, UiContext.expires_at > now_utc()
        )
    )
    return context if context and (kind is None or kind == context.kind) else None


def batch_fingerprint(reminder):
    """Detect changes to the selected cycle, including a snooze or explicit skip."""
    import hashlib

    fields = (
        "reminder_text",
        "execution_time",
        "habit_active_due_at",
        "completed_for_execution_time",
        "completed_at",
        "status",
        "snooze_count",
        "rrule_string",
        "rrule_dtstart",
        "paused_until",
        "tags",
        "priority",
        "is_nagging",
    )
    encoded = json.dumps({name: getattr(reminder, name) for name in fields}, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def batch_items(tasks, user):
    """Snapshot of *tasks* as shown to the user: id, cycle times and a
    fingerprint, so a later bulk action can tell which of them changed."""
    from bot.utils.time_ext import format_time

    return [
        {
            "id": r.id,
            "time": r.execution_time.isoformat(),
            "due": r.habit_active_due_at.isoformat() if r.habit_active_due_at else None,
            "completed": r.completed_at.isoformat() if r.completed_at else None,
            "label": r.reminder_text[:100],
            "preview": r.reminder_text[:500],
            "when": format_time(r.execution_time, user.timezone, True, "%d.%m.%Y %H:%M"),
            "selected": True,
            "fingerprint": batch_fingerprint(r),
        }
        for r in tasks
    ]
