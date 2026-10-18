"""Explicit, previewed notification presets and a non-blocking first-task offer."""

import json
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import UiContext, User
from bot.keyboards.inline import callback_button as _button
from bot.services.ui_context import create_context, get_context

router = Router(name="notification_preferences")


async def offer_notifications(message, dao, user, l10n):
    from datetime import timedelta

    from sqlalchemy import func, select

    from bot.database.models import Reminder

    # The first task, not a mandatory onboarding step. Context makes the offer durable.
    count = await dao.session.scalar(select(func.count()).select_from(Reminder).where(Reminder.user_id == user.id))
    if count != 1:
        return
    existing = await dao.session.scalar(
        select(UiContext).where(UiContext.user_id == user.id, UiContext.kind == "notify_offer")
    )
    if existing:
        return
    await create_context(dao.session, user.id, "notify_offer", {}, lifetime=timedelta(days=3650))
    builder = InlineKeyboardBuilder()
    for key, name in [("notify_only", "only"), ("notify_summaries", "summaries"), ("notify_custom", "custom")]:
        builder.row(_button(l10n[key], f"notify:{name}"))
    await message.answer(l10n["notification_offer"], reply_markup=builder.as_markup(), parse_mode=None)


@router.callback_query(F.data.startswith("notify:"))
async def preview_notifications(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    choice = callback.data.split(":")[1]
    if choice == "custom":
        from bot.keyboards.inline import get_notifications_group_keyboard

        await callback.message.answer(l10n["notify_custom"], reply_markup=get_notifications_group_keyboard(l10n))
    elif choice in {"only", "summaries"}:
        context = await create_context(reminder_dao.session, user.id, "notify", {"choice": choice})
        builder = InlineKeyboardBuilder()
        builder.row(
            _button(l10n["confirm_save"], f"notifyapply:{context.token}"), _button(l10n["btn_cancel"], "cancel_wizard")
        )
        await callback.message.edit_text(
            l10n["notify_preview_" + choice], reply_markup=builder.as_markup(), parse_mode=None
        )
    else:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("notifyapply:"))
async def apply_notifications(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    context = await get_context(reminder_dao.session, callback.data.split(":")[1], user.id, "notify")
    if not context:
        return await callback.answer(l10n["context_expired"], show_alert=True)
    if context.result is None:
        enabled = json.loads(context.payload_json)["choice"] == "summaries"
        user.briefs_enabled = enabled
        user.habit_reports_enabled = enabled
        user.missed_recovery_enabled = enabled
        context.result = "applied"
        await reminder_dao.session.commit()
    await callback.message.edit_text(l10n["notify_saved"], reply_markup=None)
    await callback.answer()
