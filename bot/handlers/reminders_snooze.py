"""Snooze actions on a fired reminder, plus the catch-all handler for a
non-text message outside any FSM flow (4.1 — split out of the original
bot/handlers/reminders.py; see bot/handlers/reminders.py's module
docstring for the full module family and why). The catch-all is
registered last within this router — and this router is included last
among the reminders_* sub-routers in bot/handlers/reminders.py — so every
more specific handler across the whole family gets first refusal.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.database.models import is_habit_like as _is_habit_like
from bot.keyboards.inline import get_snooze_keyboard, get_time_selection_keyboard
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown, escape_markdown_v2
from bot.utils.time_ext import format_time, local_time_today_or_tomorrow, to_utc_aware, to_utc_naive

router = Router(name="reminders_snooze")
logger = logging.getLogger(__name__)



@router.callback_query(F.data.startswith("snooze_show_"))
async def callback_snooze_show(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    try:
        reminder_id = int(callback.data.split("snooze_show_")[1])
    except (IndexError, ValueError) as e:
        logger.error("Malformed snooze_show callback data %r: %s", callback.data, e)
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=get_snooze_keyboard(reminder_id, l10n))
    await callback.answer()


@router.callback_query(F.data.startswith("snooze_act_"))
async def callback_snooze_act(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    state: FSMContext, user: User, l10n: dict[str, Any],
) -> None:
    try:
        parts = callback.data.split("_", 3)
        if len(parts) < 4:
            raise ValueError("Too few parts in callback data")
        reminder_id = int(parts[2])
        action = parts[3]
    except (IndexError, ValueError) as e:
        logger.error("Malformed snooze callback data %r: %s", callback.data, e)
        return await callback.answer(l10n["invalid_action"], show_alert=True)

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["task_not_found"], show_alert=True)

    if action == "custom":
        await state.set_state(ReminderWizard.choosing_time)
        await state.update_data(
            edit_reminder_id=reminder.id,
            text=reminder.reminder_text,
            tags=reminder.tags,
            priority=reminder.priority,
            is_snooze_mode=True,
        )
        await callback.message.edit_text(
            l10n["ask_time"].format(text=escape_markdown(reminder.reminder_text)),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n),
        )
        await callback.answer()
        return

    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC
    now = datetime.now(user_tz)

    delta_map = {"15m": timedelta(minutes=15), "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "2h": timedelta(hours=2), "1d": timedelta(days=1)}
    hour_map = {"morning": 9, "day": 13, "evening": 19, "night": 23}

    if action in delta_map:
        new_time = now + delta_map[action]
    elif action in hour_map:
        # 1.3: DST-safe — see local_time_today_or_tomorrow's docstring for
        # why this replaced a plain now.replace(hour=...) + timedelta(days=1).
        new_time = local_time_today_or_tomorrow(user.timezone, hour_map[action])
    else:
        await callback.answer(l10n.get("unknown_snooze", "❌ Unknown action"), show_alert=True)
        return

    new_time_utc_naive = to_utc_naive(new_time)

    # For habit-like recurring reminders, we must NOT overwrite execution_time.
    # The scheduler uses execution_time as the rrule dtstart to compute the NEXT
    # day's occurrence after the reminder fires. Overwriting it here would cause
    # every snooze to permanently shift all future occurrences (drift bug).
    # Instead, we only reschedule the current APScheduler job to fire later.
    is_habit_recurring = _is_habit_like(reminder) and reminder.is_recurring
    if not is_habit_recurring:
        reminder.execution_time = new_time_utc_naive

    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None
    reminder.snooze_count = int(reminder.snooze_count or 0) + 1
    try:
        scheduler_service.schedule_reminder(
            reminder.id,
            to_utc_aware(new_time_utc_naive),
            is_nagging=reminder.is_nagging,
        )
        scheduler_service.remove_nagging_job(reminder.id)
    except Exception:
        await reminder_dao.session.rollback()
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
        return

    # 1.3/2.2: commit BEFORE editing the confirmation message. Without
    # this, a later commit failure (DatabaseMiddleware's own implicit one)
    # would leave the job already rescheduled above pointing somewhere the
    # DB row never actually reflects.
    try:
        await reminder_dao.session.commit()
    except Exception as e:
        logger.error("Failed to commit snooze for reminder %s: %s", reminder.id, e, exc_info=True)
        await reminder_dao.session.rollback()
        try:
            # reminder.execution_time reflects the true (rolled-back)
            # committed state either way — unchanged for a habit-like
            # recurring reminder (anti-drift guard already left it alone
            # above), reverted to its pre-snooze value otherwise.
            await reminder_dao.session.refresh(reminder)
            scheduler_service.schedule_reminder(
                reminder.id, to_utc_aware(reminder.execution_time), is_nagging=reminder.is_nagging
            )
        except Exception as restore_e:
            logger.error(
                "Failed to restore prior job for reminder %s after commit failure: %s",
                reminder.id, restore_e, exc_info=True,
            )
            scheduler_service.remove_reminder_job(reminder.id)
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
        return

    if reminder.snooze_count <= 1:
        friendly_time = format_time(new_time_utc_naive, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        snoozed_line = l10n["snoozed_until"].format(time=escape_markdown_v2(friendly_time))
        # callback.message.text is None for a media message (the done-keyboard
        # is also attachable to a reminder sent with media_file_id) — fall back
        # to the caption, then an empty string, instead of escape_markdown_v2
        # crashing on None.
        original_text = callback.message.text or callback.message.caption or ""
        snooze_text = f"{escape_markdown_v2(original_text)}\n\n{snoozed_line}"
        try:
            await callback.message.edit_text(
                snooze_text,
                reply_markup=None,
                parse_mode="MarkdownV2",
            )
        except TelegramBadRequest as e:
            logger.warning("Could not edit snooze confirmation for reminder %s: %s", reminder.id, e)
    else:
        # 2nd+ snooze of the same cycle: leaving another "Postponed until
        # ..." message behind every time floods the chat over a day of
        # repeated postpones. The count itself already lives in
        # reminder.snooze_count, so just make this one disappear instead.
        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            logger.warning("Could not delete snooze confirmation for reminder %s: %s", reminder.id, e)
    await callback.answer(l10n["snoozed_toast"])


# ---------------------------------------------------------------------------
# Catch-all: unrecognized slash commands
# ---------------------------------------------------------------------------
# 2.5: reminders_wizard.py's handle_task_text excludes slash-prefixed text
# from its idle-state free-text-parses-as-task catch-all specifically so a
# command can fall through the router tree to wherever it's actually
# registered (admin.py, commands.py, donate.py, reminders_listing.py's
# /find, ...) instead of being swallowed there first. This is the true
# last resort, for a command that ISN'T registered anywhere — "/settings",
# "/stats", a typo — which otherwise got no response at all. Registered
# here (the last sub-router in the reminders_* family, itself the last
# top-level router — see bot/handlers/reminders.py and bot/handlers/
# __init__.py) so every real command handler across the whole dispatcher
# gets first refusal, same reasoning as handle_non_text_message below.

@router.message(StateFilter(None), F.text.regexp(r"^/\w+"))
async def handle_unknown_command(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(
        l10n.get("unknown_command", "❓ Unknown command. See /help for the list of commands.")
    )


# ---------------------------------------------------------------------------
# Catch-all: non-text messages outside any FSM flow
# ---------------------------------------------------------------------------
# Without this, nothing responded to a photo/voice/video/sticker/etc.
# sent with StateFilter(None) — the bot looked broken, silently swallowing
# the update. Registered last so every more specific handler (forwarded
# messages, FSM-state text handlers, callbacks) gets first refusal; ~F.text
# means an ordinary text message never reaches this either way, since
# handle_task_text above already claims those.

@router.message(StateFilter(None), ~F.text)
async def handle_non_text_message(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(l10n.get("text_only_hint", "📝 I can only understand text right now. Send your reminder as a text message."))
