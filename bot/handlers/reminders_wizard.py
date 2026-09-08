"""Reminder creation wizard: free-text entry -> time selection -> save, plus
the parse-confidence confirmation step and re-opening the time picker from
an existing reminder's edit screen (4.1 — split out of the original
bot/handlers/reminders.py; see bot/handlers/reminders.py's module
docstring for the full module family and why).
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.reminders_shared import (
    _MAX_INPUT,
    _MENU_TEXTS,
    _handle_parsed_result,
    _reset_auto_delete,
    _save_and_show_edit,
)
from bot.keyboards.inline import get_time_selection_keyboard
from bot.services.parser import InputParser
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown
from bot.utils.time_ext import local_time_tomorrow

router = Router(name="reminders_wizard")
parser = InputParser()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FSM: text input
# ---------------------------------------------------------------------------
# NOTE: the "New Task" / "My Tasks" main-menu button handlers live in
# bot/handlers/menu.py (registered on an earlier router) so they can't be
# swallowed by another router's stateful FSM handlers.

@router.message(StateFilter(None), F.forward_origin)
async def handle_forwarded_task(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Extract text from a forwarded message and route it through the wizard."""
    text = message.text or message.caption
    if not text:
        # REWORK_PLAN_3 2.7: a forwarded photo/video/voice with no caption
        # used to get silently dropped here — no error, no hint, nothing.
        await message.answer(l10n.get("text_only_hint", "📝 I can only understand text right now. Send your reminder as a text message."))
        return

    await state.clear()

    origin_name = ""
    fwd = message.forward_origin
    if fwd:
        if fwd.type == "user":
            origin_name = fwd.sender_user.full_name
        elif fwd.type == "hidden_user":
            origin_name = fwd.sender_user_name
        elif fwd.type == "channel":
            origin_name = fwd.chat.title
        elif fwd.type == "chat":
            origin_name = fwd.sender_chat.title if getattr(fwd, "sender_chat", None) else "Group"

    if origin_name:
        prefix = f"👤 {l10n.get('forwarded_from', 'Forwarded from')} {origin_name}:\n"
    else:
        prefix = ""

    full_text = f"{prefix}{text}".strip()

    if len(full_text) > _MAX_INPUT:
        await message.answer(l10n.get("text_too_long", "❌ Text too long.").format(length=len(full_text), max_length=_MAX_INPUT))
        return

    try:
        result = await parser.parse(full_text, user.timezone)
        await _handle_parsed_result(message, state, user, l10n, result, reminder_dao, scheduler_service)
    except ValueError as ve:
        await message.answer(str(ve))
    except Exception as e:
        logger.error("Error parsing forwarded text: %s", e, exc_info=True)
        await message.answer(l10n.get("parse_error", "Error parsing text"))
        await state.clear()


@router.message(ReminderWizard.entering_text, F.text)
@router.message(StateFilter(None), F.text)
async def handle_task_text(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Catch-all: parse any text message as a potential reminder."""
    if message.text in _MENU_TEXTS:
        return
    if len(message.text) > _MAX_INPUT:
        await message.answer(l10n.get("text_too_long", "❌ Text too long.").format(length=len(message.text), max_length=_MAX_INPUT))
        return

    try:
        result = await parser.parse(message.text, user.timezone)
        # P1-11: reminder text can carry medical/personal/otherwise sensitive
        # content — never log the raw or cleaned text itself at INFO level.
        # Length and whether a datetime/how confidently it was found is
        # enough to debug the parser without exposing what the user wrote.
        logger.info(
            "Parsed message for user %s: input_len=%d clean_len=%d dt_found=%s confidence=%.2f",
            user.id,
            len(message.text),
            len(result.clean_text),
            result.parsed_datetime is not None,
            result.confidence,
        )
        await _handle_parsed_result(message, state, user, l10n, result, reminder_dao, scheduler_service)
    except ValueError as ve:
        await message.answer(str(ve))
    except Exception as e:
        logger.error(
            "Error parsing message for user %s (input_len=%d): %s",
            user.id,
            len(message.text),
            e,
            exc_info=True,
        )
        await message.answer(l10n["parse_error"])


# ---------------------------------------------------------------------------
# FSM: time selection
# ---------------------------------------------------------------------------

@router.message(ReminderWizard.choosing_time, F.text)
async def state_choosing_time_text_input(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Accept a typed time expression while the time-selection keyboard is
    showing (REWORK_PLAN_3 2.1). Without this, a user who types instead of
    tapping a button — including after tapping "⌨️ Enter manually", whose
    only purpose is to invite exactly that — got no response at all: no
    error, no retry prompt, nothing.

    The task description is already fixed in `state` from the step that led
    here (_handle_parsed_result or callback_edit_edit / callback_snooze_act);
    this only extracts a datetime from the typed text, discarding whatever
    `parser.parse` produced as clean_text.
    """
    if message.text in _MENU_TEXTS:
        return
    if len(message.text) > _MAX_INPUT:
        await message.answer(l10n.get("text_too_long", "❌ Text too long.").format(length=len(message.text), max_length=_MAX_INPUT))
        return

    try:
        result = await parser.parse(message.text, user.timezone)
    except Exception as e:
        logger.error("Error parsing time input for user %s: %s", user.id, e, exc_info=True)
        await message.answer(l10n["parse_error"])
        return

    if not result.parsed_datetime:
        await message.answer(
            l10n.get("choosing_time_retry", "🕒 I couldn't find a time in that. Try again, e.g. `18:30` or `tomorrow at 9`."),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset),
        )
        return

    await state.update_data(execution_time=result.parsed_datetime.isoformat())
    await _save_and_show_edit(message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.choosing_time, F.data.startswith("time_"))
async def callback_time_selected(
    callback: CallbackQuery, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Resolve the chosen time option and persist the reminder."""
    data_str = callback.data
    try:
        tz = pytz.timezone(user.timezone)
    except Exception:
        tz = pytz.UTC
    now = datetime.now(tz)
    execution_time = None

    if "delta" in data_str:
        minutes = int(data_str.split("_")[-1])
        execution_time = now + timedelta(minutes=minutes)
    elif "fixed" in data_str:
        execution_time = datetime.fromisoformat(data_str.split("_fixed_")[1])
    elif "tomorrow" in data_str:
        # 1.3: DST-safe — see local_time_tomorrow's docstring for why this
        # replaced a plain now.replace(hour=9, ...) + timedelta(days=1).
        execution_time = local_time_tomorrow(user.timezone, 9)
    elif "manual" in data_str:
        # REWORK_PLAN_3 2.2: used to state.clear() here, discarding the task
        # text and (for an edit/snooze) edit_reminder_id, and prompting the
        # user to "try again" with no working way to actually enter a time —
        # state_choosing_time_text_input didn't exist yet. Now that it does,
        # stay in choosing_time: the state data survives, and the next thing
        # the user types is picked up as a time expression by that handler.
        await callback.answer()
        await callback.message.edit_text(l10n["try_again_manual"], reply_markup=None)
        return

    if execution_time:
        await state.update_data(execution_time=execution_time.isoformat())
        await callback.answer()
        await callback.message.delete()
        await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)
    else:
        await callback.answer(l10n.get("parse_error", "❌ Unknown option"), show_alert=True)
        await state.clear()


@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_yes")
async def callback_parse_confirm_yes(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    await callback.answer()
    await callback.message.delete()
    await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_pick_time")
async def callback_parse_confirm_pick_time(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    text = data.get("text", l10n.get("task_untitled", "Untitled task"))
    await state.set_state(ReminderWizard.choosing_time)
    await callback.message.edit_text(
        l10n["ask_time"].format(text=escape_markdown(text)),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset),
    )
    await callback.answer()


@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_cancel")
async def callback_parse_confirm_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await callback.message.edit_text(l10n.get("cmd_cancel", "Reminder creation cancelled."), reply_markup=None)
    await callback.answer()


@router.message(ReminderWizard.confirming_parse, F.text)
async def state_confirming_parse_new_text(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """User typed new text instead of tapping a confirm button — treat it as a fresh task."""
    await state.clear()
    await handle_task_text(message, state, user, l10n, reminder_dao, scheduler_service)


# ---------------------------------------------------------------------------
# Edit keyboard callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit_edit_"))
async def callback_edit_edit(
    callback: CallbackQuery, reminder_dao: ReminderDAO, state: FSMContext, l10n: dict[str, Any], user: User
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_edit_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    await state.set_state(ReminderWizard.choosing_time)
    # 4.3: carry the existing tags/priority forward — this flow only
    # changes the time, so _save_and_show_edit must not wipe them.
    await state.update_data(
        edit_reminder_id=reminder.id,
        text=reminder.reminder_text,
        tags=reminder.tags,
        priority=reminder.priority,
    )
    await callback.message.edit_text(
        l10n["ask_time"].format(text=escape_markdown(reminder.reminder_text)),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n),
    )
    await callback.answer()

