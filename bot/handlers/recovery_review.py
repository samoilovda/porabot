"""Owner-scoped recovery previews and idempotent batch confirmation."""

import json
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import UiContext, User
from bot.keyboards.inline import callback_button as _button
from bot.services.scheduler import SchedulerService
from bot.services.ui_context import create_context, get_context
from bot.utils.time_ext import format_time

router = Router(name="recovery_review")


async def begin_batch(callback, dao, user, l10n):
    from bot.services.missed_recovery import RECOVERY_DIGEST_LIMIT

    tasks = await dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=RECOVERY_DIGEST_LIMIT)
    payload = {
        "items": [
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
    }
    context = await create_context(dao.session, user.id, "batch", payload)
    await render_batch(callback.message, context, l10n)
    await callback.answer()


async def render_batch(message, context, l10n):
    items = json.loads(context.payload_json)["items"]
    builder = InlineKeyboardBuilder()
    for i, item in enumerate(items):
        builder.row(
            _button(
                ("☑ " if item["selected"] else "☐ ") + f"{i + 1}. " + item["label"], f"batch:{context.token}:toggle:{i}"
            )
        )
    builder.row(
        _button(l10n["batch_apply"], f"batch:{context.token}:apply:0"), _button(l10n["btn_cancel"], "cancel_wizard")
    )
    await message.edit_text(
        l10n["batch_preview"].format(count=sum(i["selected"] for i in items))
        + "\n\n"
        + "\n\n".join(f"{i + 1}. {item['preview']}\n{item['when']}" for i, item in enumerate(items)),
        reply_markup=builder.as_markup(),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("batch:"))
async def batch_action(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    from sqlalchemy import update

    try:
        _, token, action, raw_index = callback.data.split(":")
        index = int(raw_index)
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    session = reminder_dao.session
    context = await get_context(session, token, user.id, "batch")
    if context is None:
        return await callback.answer(l10n["context_expired"], show_alert=True)
    if context.result is not None:
        return await callback.answer(l10n["recovery_done_all_done"].format(count=context.result), show_alert=True)
    payload = json.loads(context.payload_json)
    if action == "toggle":
        if not 0 <= index < len(payload["items"]):
            return await callback.answer(l10n["invalid_action"], show_alert=True)
        payload["items"][index]["selected"] = not payload["items"][index]["selected"]
        context.payload_json = json.dumps(payload, ensure_ascii=False)
        await session.commit()
        await render_batch(callback.message, context, l10n)
        return await callback.answer()
    if action != "apply":
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    # Claim and domain writes share one transaction. Another process cannot apply it twice.
    claimed = await session.execute(
        update(UiContext)
        .where(UiContext.token == token, UiContext.user_id == user.id, UiContext.result.is_(None))
        .values(result="processing")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return await callback.answer(l10n["already_done"], show_alert=True)
    tasks = []
    for item in payload["items"]:
        if not item["selected"]:
            continue
        r = await reminder_dao.get_owned(item["id"], user.id)
        if (
            r
            and r.status == "pending"
            and r.execution_time.isoformat() == item["time"]
            and (r.habit_active_due_at.isoformat() if r.habit_active_due_at else None) == item["due"]
            and (r.completed_at.isoformat() if r.completed_at else None) == item["completed"]
            and batch_fingerprint(r) == item["fingerprint"]
        ):
            tasks.append(r)
    context.result = str(len(tasks))
    if not tasks:
        await session.commit()
        await callback.message.edit_text(
            l10n["recovery_done_all_done"].format(count=0)
            + "\n"
            + l10n["batch_skipped"].format(count=sum(i["selected"] for i in payload["items"])),
            reply_markup=None,
        )
        return await callback.answer()
    from bot.handlers.reminders_listing import callback_recovery_done_all

    await callback_recovery_done_all(
        callback,
        reminder_dao,
        habit_event_dao,
        scheduler_service,
        user,
        l10n,
        overdue_snapshot=tasks,
        batch_context=context,
    )


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
