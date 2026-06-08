"""
Reminder Handlers — FSM wizard for reminder creation and management.

FSM states (ReminderWizard):
  entering_text  → waiting for reminder text
  choosing_time  → showing time-selection keyboard

All handlers receive dependencies via DatabaseMiddleware injection:
  user, reminder_dao, scheduler_service, l10n, state
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    DeleteTaskCallback,
    DoneNoteCallback,
    DoneSkipNextCallback,
    DoneTaskCallback,
    DoneUndoCallback,
    EditReminderCallback,
    SnoozeActCallback,
    SnoozeShowCallback,
    TaskSettingsCallback,
    TimeDeltaCallback,
    TimeFixedCallback,
)
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import ReminderStatus, User
from bot.keyboards.inline import (
    get_completed_tasks_keyboard,
    get_done_followup_keyboard,
    get_edit_keyboard,
    get_parse_confirmation_keyboard,
    get_snooze_keyboard,
    get_tasks_list_keyboard,
    get_time_selection_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.parser import InputParser
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.habits_utils import is_habit_like
from bot.utils.markdown import escape_markdown_v2
from bot.utils.recurrence import next_rrule_occurrence
from bot.utils.time_ext import format_time, resolve_tz, to_utc_aware, to_utc_naive

router = Router(name="reminders")
parser = InputParser()
logger = logging.getLogger(__name__)

# asyncio.Task registry for auto-removing inline keyboards after a short delay
active_auto_delete_tasks: dict[tuple[int, int], asyncio.Task] = {}

_MENU_TEXTS = frozenset([
    "➕ Новая задача", "📅 Мои задачи", "⚙️ Настройки",
    "➕ New Task", "📅 My Tasks", "⚙️ Settings",
    "➕ Nueva tarea", "📅 Mis tareas", "⚙️ Ajustes",
    "🫧 Привычки", "🫧 Habits", "🫧 Hábitos",
])
_MAX_INPUT = 3000
_NAG_LIMIT_MIN = 0
_NAG_LIMIT_MAX = 20
_COMPLETED_HISTORY_DAYS = 7
_PARSE_CONFIDENCE_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rrule_text(reminder, l10n: dict[str, Any]) -> str:
    """Return a human-readable recurrence label for *reminder*."""
    if not reminder.is_recurring or not reminder.rrule_string:
        return l10n["repeat_none"]
    if "DAILY" in reminder.rrule_string:
        return l10n["repeat_day"]
    if "BYDAY" in reminder.rrule_string:
        return l10n["repeat_weekdays"]
    if "WEEKLY" in reminder.rrule_string:
        return l10n["repeat_week"]
    return l10n["repeat_none"]


def _message_task_key(message: Message) -> tuple[int, int]:
    """Use chat+message id to avoid cross-chat key collisions."""
    return (message.chat.id, message.message_id)


async def _remove_keyboard_after_delay(message: Message, delay: int = 5) -> None:
    """Background task: remove an inline keyboard after *delay* seconds."""
    try:
        await asyncio.sleep(delay)
        await message.edit_reply_markup(reply_markup=None)
    except asyncio.CancelledError:
        pass
    except TelegramBadRequest:
        pass
    finally:
        active_auto_delete_tasks.pop(_message_task_key(message), None)


def _reset_auto_delete(message: Message) -> None:
    """Cancel the pending keyboard-removal task for *message* (if any)."""
    task = active_auto_delete_tasks.get(_message_task_key(message))
    if task and not task.done():
        task.cancel()


def _format_parse_confidence(confidence: float) -> int:
    return max(0, min(100, int(round(confidence * 100))))


def _pick_done_reply(l10n: dict[str, Any]) -> str:
    """Return a randomized done-reply phrase with compatibility fallback."""
    options = l10n.get("task_done_replies")
    if isinstance(options, list):
        normalized = [str(item) for item in options if item]
        if normalized:
            return random.choice(normalized)
    return str(l10n.get("task_done_reply", "✅ **Great!**"))


async def _schedule_reminder_safe(
    *,
    reminder_id: int,
    execution_time: datetime,
    is_nagging: bool,
    scheduler_service: SchedulerService,
    session: AsyncSession,
    callback: CallbackQuery,
    l10n: dict[str, Any],
) -> bool:
    """Schedule a reminder; on failure rollback the session and answer with an error.

    Returns True on success, False on failure (caller should return early).
    """
    try:
        scheduler_service.schedule_reminder(
            reminder_id,
            to_utc_aware(execution_time),
            is_nagging=is_nagging,
        )
        return True
    except Exception as e:
        logger.error("Failed to schedule reminder %s: %s", reminder_id, e, exc_info=True)
        await session.rollback()
        await callback.answer(
            l10n.get("schedule_error", "❌ Failed to schedule. Please try again."),
            show_alert=True,
        )
        return False


async def _handle_parsed_result(
    source_message: Message,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    result,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    clean_text = result.clean_text or l10n.get("task_untitled", "Untitled task")
    await state.update_data(text=clean_text, user_timezone=user.timezone, chat_id=source_message.chat.id)

    if result.parsed_datetime:
        await state.update_data(execution_time=result.parsed_datetime.isoformat())
        if float(getattr(result, "confidence", 0.0) or 0.0) < _PARSE_CONFIDENCE_THRESHOLD:
            await state.set_state(ReminderWizard.confirming_parse)
            parsed_time = format_time(
                result.parsed_datetime,
                user.timezone,
                user.show_utc_offset,
                "%d.%m.%Y %H:%M",
            )
            await source_message.answer(
                l10n["parse_confirmation_prompt"].format(
                    text=clean_text,
                    time=parsed_time,
                    confidence=_format_parse_confidence(float(getattr(result, "confidence", 0.0) or 0.0)),
                ),
                reply_markup=get_parse_confirmation_keyboard(l10n),
            )
            return
        await _save_and_show_edit(source_message, state, l10n, user, reminder_dao, scheduler_service)
        return

    await state.set_state(ReminderWizard.choosing_time)
    await source_message.answer(
        l10n["ask_time"].format(text=clean_text),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset),
    )


async def _save_and_show_edit(
    source_message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    """Persist reminder to DB, schedule it, send confirmation with edit keyboard."""
    data = await state.get_data()
    text = data.get("text")
    execution_time_raw = datetime.fromisoformat(data["execution_time"])
    execution_time = to_utc_naive(execution_time_raw)
    edit_reminder_id = data.get("edit_reminder_id")

    if edit_reminder_id:
        new_reminder = await reminder_dao.get_by_id(edit_reminder_id)
        if not new_reminder or new_reminder.user_id != user.id:
            logger.warning("Reminder %s not found or not owned by user %s during edit.", edit_reminder_id, user.id)
            return
        new_reminder.reminder_text = text
        new_reminder.execution_time = execution_time
    else:
        try:
            new_reminder = await reminder_dao.create_reminder(
                user_id=user.id,
                text=text,
                execution_time=execution_time,
                is_recurring=False,
                rrule_string=None,
                is_nagging=False,
            )
        except ValueError as ve:
            logger.warning("Validation error for user %s: %s", user.id, ve)
            await source_message.answer(str(ve))
            await state.clear()
            return

    try:
        scheduler_service.schedule_reminder(
            new_reminder.id,
            to_utc_aware(new_reminder.execution_time),
            is_nagging=new_reminder.is_nagging,
        )
    except Exception as e:
        logger.error("Failed to schedule reminder %s: %s", new_reminder.id, e, exc_info=True)
        await reminder_dao.session.rollback()
        await source_message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        await state.clear()
        return

    await state.clear()

    date_str = format_time(execution_time, user.timezone, user.show_utc_offset, "%d.%m.%Y %H:%M")
    preview = l10n["preview"].format(text=new_reminder.reminder_text, time=date_str)
    safe_preview = escape_markdown_v2(preview)
    keyboard = get_edit_keyboard(
        reminder_id=new_reminder.id,
        l10n=l10n,
        is_recurring=new_reminder.is_recurring,
        is_nagging=new_reminder.is_nagging,
        nagging_max_repeats=new_reminder.nagging_max_repeats,
        rrule_text=_rrule_text(new_reminder, l10n),
    )
    sent_msg = await source_message.answer(safe_preview, reply_markup=keyboard, parse_mode="MarkdownV2")

    task = asyncio.create_task(_remove_keyboard_after_delay(sent_msg, 5))
    active_auto_delete_tasks[_message_task_key(sent_msg)] = task


# ---------------------------------------------------------------------------
# Main menu buttons
# ---------------------------------------------------------------------------

@router.message(F.text.in_(["➕ Новая задача", "➕ New Task", "➕ Nueva tarea"]))
async def btn_new_task(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(ReminderWizard.entering_text)
    await message.answer(l10n["enter_task"], parse_mode="Markdown")


@router.message(F.text.in_(["📅 Мои задачи", "📅 My Tasks", "📅 Mis tareas"]))
async def btn_my_tasks(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    await state.clear()
    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await message.answer(l10n["no_tasks"], reply_markup=get_main_menu_keyboard(l10n))
        return

    lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        lines.append(f"▫️ `{dt_str}`: {'🔁 ' if task.is_recurring else ''}{'🔥 ' if task.is_nagging else ''}{task.reminder_text}")

    safe_text = escape_markdown_v2("\n".join(lines))
    await message.answer(safe_text, reply_markup=get_tasks_list_keyboard(tasks, l10n), parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# FSM: text input
# ---------------------------------------------------------------------------

@router.message(F.forward_origin)
async def handle_forwarded_task(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Extract text from a forwarded message and route it through the wizard."""
    text = message.text or message.caption
    if not text:
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
            origin_name = getattr(fwd, "sender_chat").title if getattr(fwd, "sender_chat", None) else "Group"

    prefix = f"👤 {'Forwarded from' if user.language == 'en' else 'Переслано от'} {origin_name}:\n" if origin_name else ""
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
        logger.info("Parsed '%s' → clean='%s' dt=%s", message.text, result.clean_text, result.parsed_datetime)
        await _handle_parsed_result(message, state, user, l10n, result, reminder_dao, scheduler_service)
    except ValueError as ve:
        await message.answer(str(ve))
    except Exception as e:
        logger.error("Error parsing text '%s': %s", message.text, e, exc_info=True)
        await message.answer(l10n["parse_error"])


# ---------------------------------------------------------------------------
# FSM: time selection (four dedicated handlers replace the old combined one)
# ---------------------------------------------------------------------------

@router.callback_query(ReminderWizard.choosing_time, TimeDeltaCallback.filter())
async def callback_time_delta(
    callback: CallbackQuery,
    callback_data: TimeDeltaCallback,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    tz = resolve_tz(user.timezone)
    execution_time = datetime.now(tz) + timedelta(minutes=callback_data.minutes)
    await state.update_data(execution_time=execution_time.isoformat())
    await callback.answer()
    await callback.message.delete()
    await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.choosing_time, TimeFixedCallback.filter())
async def callback_time_fixed(
    callback: CallbackQuery,
    callback_data: TimeFixedCallback,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    execution_time = datetime.fromtimestamp(callback_data.ts, tz=timezone.utc)
    await state.update_data(execution_time=execution_time.isoformat())
    await callback.answer()
    await callback.message.delete()
    await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.choosing_time, F.data == "time_tomorrow")
async def callback_time_tomorrow(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    tz = resolve_tz(user.timezone)
    now = datetime.now(tz)
    execution_time = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    await state.update_data(execution_time=execution_time.isoformat())
    await callback.answer()
    await callback.message.delete()
    await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.choosing_time, F.data == "time_manual")
async def callback_time_manual(
    callback: CallbackQuery, l10n: dict[str, Any]
) -> None:
    await callback.answer()
    await callback.message.edit_text(l10n["try_again_manual"])


# ---------------------------------------------------------------------------
# Parse-confirmation callbacks
# ---------------------------------------------------------------------------

@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_yes")
async def callback_parse_confirm_yes(
    callback: CallbackQuery, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    await callback.answer()
    await callback.message.delete()
    await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)


@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_pick_time")
async def callback_parse_confirm_pick_time(
    callback: CallbackQuery, state: FSMContext, user: User, l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    text = data.get("text", l10n.get("task_untitled", "Untitled task"))
    await state.set_state(ReminderWizard.choosing_time)
    await callback.message.edit_text(
        l10n["ask_time"].format(text=text),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset),
    )
    await callback.answer()


@router.callback_query(ReminderWizard.confirming_parse, F.data == "parse_confirm_cancel")
async def callback_parse_confirm_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await callback.message.edit_text(l10n.get("cmd_cancel", "Reminder creation cancelled."), reply_markup=None)
    await callback.answer()


# ---------------------------------------------------------------------------
# Edit keyboard callbacks (typed via EditReminderCallback)
# ---------------------------------------------------------------------------

@router.callback_query(EditReminderCallback.filter(F.action == "toggle_repeat"))
async def callback_edit_repeat(
    callback: CallbackQuery,
    callback_data: EditReminderCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    _reset_auto_delete(callback.message)
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    options = {
        l10n["repeat_none"]:     (False, None),
        l10n["repeat_day"]:      (True, "FREQ=DAILY"),
        l10n["repeat_weekdays"]: (True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        l10n["repeat_week"]:     (True, "FREQ=WEEKLY"),
    }
    current_key = next(
        (k for k, v in options.items() if reminder.is_recurring and reminder.rrule_string == v[1]),
        l10n["repeat_none"],
    )
    keys = list(options)
    next_key = keys[(keys.index(current_key) + 1) % len(keys)]
    is_rec, rrule = options[next_key]

    reminder.is_recurring = is_rec
    reminder.rrule_string = rrule

    ok = await _schedule_reminder_safe(
        reminder_id=reminder.id,
        execution_time=reminder.execution_time,
        is_nagging=reminder.is_nagging,
        scheduler_service=scheduler_service,
        session=reminder_dao.session,
        callback=callback,
        l10n=l10n,
    )
    if not ok:
        return

    await callback.message.edit_reply_markup(
        reply_markup=get_edit_keyboard(
            reminder.id, l10n, is_rec, reminder.is_nagging, reminder.nagging_max_repeats, next_key,
        )
    )
    await callback.answer()


@router.callback_query(EditReminderCallback.filter(F.action == "toggle_nagging"))
async def callback_edit_nagging(
    callback: CallbackQuery,
    callback_data: EditReminderCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    _reset_auto_delete(callback.message)
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    reminder.is_nagging = not reminder.is_nagging

    ok = await _schedule_reminder_safe(
        reminder_id=reminder.id,
        execution_time=reminder.execution_time,
        is_nagging=reminder.is_nagging,
        scheduler_service=scheduler_service,
        session=reminder_dao.session,
        callback=callback,
        l10n=l10n,
    )
    if not ok:
        return

    if not reminder.is_nagging:
        reminder.nagging_sent_count = 0
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None
        scheduler_service.remove_nagging_job(reminder.id)

    await callback.message.edit_reply_markup(
        reply_markup=get_edit_keyboard(
            reminder.id, l10n, reminder.is_recurring, reminder.is_nagging,
            reminder.nagging_max_repeats, _rrule_text(reminder, l10n),
        )
    )
    await callback.answer()


@router.callback_query(EditReminderCallback.filter(F.action == "delete"))
async def callback_edit_delete(
    callback: CallbackQuery,
    callback_data: EditReminderCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    _reset_auto_delete(callback.message)
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    await reminder_dao.delete_by_id(callback_data.reminder_id)
    scheduler_service.remove_reminder_job(callback_data.reminder_id)
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


@router.callback_query(EditReminderCallback.filter(F.action == "set_nag_limit"))
async def callback_edit_set_nag_limit(
    callback: CallbackQuery,
    callback_data: EditReminderCallback,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    _reset_auto_delete(callback.message)
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    await state.set_state(ReminderWizard.waiting_for_nag_limit)
    await state.update_data(nag_limit_reminder_id=reminder.id)
    await callback.message.answer(
        l10n["nagging_limit_prompt"].format(
            count=max(0, int(reminder.nagging_max_repeats)),
            min=_NAG_LIMIT_MIN,
            max=_NAG_LIMIT_MAX,
        )
    )
    await callback.answer()


@router.message(ReminderWizard.waiting_for_nag_limit, F.text)
async def state_nag_limit(
    message: Message,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    raw_value = message.text.strip() if message.text else ""
    try:
        nag_limit = int(raw_value)
    except ValueError:
        await message.answer(l10n["nagging_limit_invalid"].format(min=_NAG_LIMIT_MIN, max=_NAG_LIMIT_MAX))
        return

    if nag_limit < _NAG_LIMIT_MIN or nag_limit > _NAG_LIMIT_MAX:
        await message.answer(l10n["nagging_limit_invalid"].format(min=_NAG_LIMIT_MIN, max=_NAG_LIMIT_MAX))
        return

    state_data = await state.get_data()
    reminder_id = state_data.get("nag_limit_reminder_id")
    if not reminder_id:
        await state.clear()
        await message.answer(l10n.get("parse_error", "Error parsing text. Check the format."))
        return

    reminder = await reminder_dao.get_by_id(int(reminder_id))
    if not reminder:
        await state.clear()
        await message.answer(l10n["item_not_found"])
        return

    reminder.nagging_max_repeats = nag_limit
    reminder.nagging_sent_count = min(max(0, int(reminder.nagging_sent_count)), nag_limit)
    if not reminder.is_nagging or nag_limit == 0 or reminder.nagging_sent_count >= nag_limit:
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None
        scheduler_service.remove_nagging_job(reminder.id)

    await state.clear()
    await message.answer(
        l10n["nagging_limit_updated"].format(count=nag_limit),
        reply_markup=get_edit_keyboard(
            reminder.id, l10n, reminder.is_recurring, reminder.is_nagging,
            reminder.nagging_max_repeats, _rrule_text(reminder, l10n),
        ),
    )


# ---------------------------------------------------------------------------
# Task list callbacks
# ---------------------------------------------------------------------------

@router.callback_query(TaskSettingsCallback.filter())
async def callback_task_settings(
    callback: CallbackQuery,
    callback_data: TaskSettingsCallback,
    reminder_dao: ReminderDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    await callback.message.answer(
        l10n["task_settings_title"].format(text=reminder.reminder_text),
        reply_markup=get_edit_keyboard(
            reminder.id, l10n, reminder.is_recurring, reminder.is_nagging,
            reminder.nagging_max_repeats, _rrule_text(reminder, l10n),
        ),
    )
    await callback.answer()


@router.callback_query(DeleteTaskCallback.filter())
async def callback_delete_task(
    callback: CallbackQuery,
    callback_data: DeleteTaskCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    await reminder_dao.delete_by_id(callback_data.reminder_id)
    scheduler_service.remove_reminder_job(callback_data.reminder_id)
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


@router.callback_query(F.data == "close_tasks")
async def callback_close_tasks(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.delete()


@router.callback_query(F.data == "refresh_tasks")
async def callback_refresh_tasks(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await callback.message.edit_text(l10n["no_tasks"], reply_markup=None)
        await callback.answer()
        return

    lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        lines.append(f"▫️ `{dt_str}`: {'🔁 ' if task.is_recurring else ''}{'🔥 ' if task.is_nagging else ''}{task.reminder_text}")

    safe_text = escape_markdown_v2("\n".join(lines))
    await callback.message.edit_text(safe_text, reply_markup=get_tasks_list_keyboard(tasks, l10n), parse_mode="MarkdownV2")
    await callback.answer()


@router.callback_query(F.data == "recovery_done_all")
async def callback_recovery_done_all(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    overdue = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=100)
    if not overdue:
        await callback.answer(l10n.get("no_tasks", "No tasks"), show_alert=True)
        return

    now_utc = datetime.now(timezone.utc)
    for task in overdue:
        await reminder_dao.mark_done(task.id)
        if task.is_recurring and task.rrule_string:
            next_run = next_rrule_occurrence(task.rrule_string, task.execution_time, now_utc)
            if next_run:
                next_run_utc_naive = to_utc_naive(next_run)
                task.execution_time = next_run_utc_naive
                task.completed_for_execution_time = None
                ok = await _schedule_reminder_safe(
                    reminder_id=task.id,
                    execution_time=next_run_utc_naive,
                    is_nagging=task.is_nagging,
                    scheduler_service=scheduler_service,
                    session=reminder_dao.session,
                    callback=callback,
                    l10n=l10n,
                )
                if not ok:
                    return
            else:
                scheduler_service.remove_reminder_job(task.id)
        else:
            scheduler_service.remove_reminder_job(task.id)
        scheduler_service.remove_nagging_job(task.id)

    await callback.message.edit_text(
        l10n.get("recovery_done_all_done", "✅ Marked {count} overdue tasks as done.").format(count=len(overdue)),
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "recovery_snooze_all")
async def callback_recovery_snooze_all(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    overdue = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=100)
    if not overdue:
        await callback.answer(l10n.get("no_tasks", "No tasks"), show_alert=True)
        return

    new_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    # Schedule all jobs first so we don't mutate ORM state if scheduling fails mid-way
    for task in overdue:
        ok = await _schedule_reminder_safe(
            reminder_id=task.id,
            execution_time=new_time,
            is_nagging=task.is_nagging,
            scheduler_service=scheduler_service,
            session=reminder_dao.session,
            callback=callback,
            l10n=l10n,
        )
        if not ok:
            return

    for task in overdue:
        task.execution_time = new_time
        task.completed_for_execution_time = None
        task.last_nag_chat_id = None
        task.last_nag_message_id = None
        scheduler_service.remove_nagging_job(task.id)

    await callback.message.edit_text(
        l10n.get("recovery_snooze_all_done", "⏰ Snoozed {count} overdue tasks by 1 hour.").format(count=len(overdue)),
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "show_completed")
async def callback_show_completed(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    completed = await reminder_dao.get_recent_completed_tasks(user.id, user.timezone, days=_COMPLETED_HISTORY_DAYS)
    if not completed:
        await callback.answer(l10n["no_completed_tasks"], show_alert=True)
        return

    lines = [l10n["completed_header"]]
    for task in completed:
        completed_dt = task.completed_at or task.execution_time
        dt_str = format_time(completed_dt, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        note_suffix = f" — {task.last_completion_note}" if task.last_completion_note else ""
        lines.append(f"✅ `{dt_str}`: ~{task.reminder_text}~{note_suffix}")

    safe_text = escape_markdown_v2("\n".join(lines))
    await callback.message.edit_text(safe_text, reply_markup=get_completed_tasks_keyboard(l10n), parse_mode="MarkdownV2")
    await callback.answer()


# ---------------------------------------------------------------------------
# Mark done
# ---------------------------------------------------------------------------

@router.callback_query(DoneTaskCallback.filter())
async def callback_task_done(
    callback: CallbackQuery,
    callback_data: DoneTaskCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder_id = callback_data.reminder_id
    cycle_due_at_utc_naive = None
    if callback_data.cycle_due_ts is not None:
        cycle_due_at_utc_naive = datetime.fromtimestamp(callback_data.cycle_due_ts, tz=timezone.utc).replace(tzinfo=None)

    reminder = await reminder_dao.get_by_id(reminder_id)

    if not reminder or reminder.user_id != user.id:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return

    # Idempotency: ignore rapid double-taps
    if reminder.status == ReminderStatus.COMPLETED or (
        reminder.is_recurring
        and reminder.completed_for_execution_time is not None
        and reminder.completed_for_execution_time >= reminder.execution_time
    ):
        await callback.answer(l10n.get("already_done", "Already done ✅"))
        return

    if getattr(reminder, "is_fluid_habit", False):
        newly_done = await reminder_dao.mark_fluid_habit_done_today(reminder.id, user.timezone)
        scheduler_service.remove_nagging_job(reminder.id)
        if not newly_done:
            await callback.answer(l10n.get("already_done", "Already done ✅"))
            return
        try:
            done_text = escape_markdown_v2(f"{callback.message.text}\n\n{_pick_done_reply(l10n)}")
            await callback.message.edit_text(done_text, reply_markup=None, parse_mode="MarkdownV2")
        except TelegramBadRequest:
            pass
        await callback.answer(l10n["btn_done"])
        return

    if is_habit_like(reminder):
        due_at = cycle_due_at_utc_naive or reminder.habit_active_due_at
        if due_at is not None:
            now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            streak_result = await reminder_dao.apply_habit_streak_completion(
                reminder.id,
                due_at_utc_naive=due_at,
                completed_at_utc_naive=now_utc_naive,
            )
            if streak_result.get("already_counted"):
                await callback.answer(l10n.get("already_done", "Already done ✅"))
                return

    await reminder_dao.mark_done(reminder_id)
    if not reminder.is_recurring:
        scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)

    try:
        done_text = escape_markdown_v2(f"{callback.message.text}\n\n{_pick_done_reply(l10n)}")
        await callback.message.edit_text(
            done_text,
            reply_markup=get_done_followup_keyboard(
                reminder_id=reminder.id,
                l10n=l10n,
                is_recurring=bool(reminder.is_recurring),
            ),
            parse_mode="MarkdownV2",
        )
    except TelegramBadRequest:
        pass  # Concurrent tap — safe to ignore

    await callback.answer(l10n["btn_done"])


@router.callback_query(F.data == "done_close")
async def callback_done_close(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(DoneNoteCallback.filter())
async def callback_done_note(
    callback: CallbackQuery,
    callback_data: DoneNoteCallback,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    await state.set_state(ReminderWizard.waiting_for_done_note)
    await state.update_data(done_note_reminder_id=reminder.id)
    await callback.message.answer(l10n.get("done_note_prompt", "Send a short completion note."))
    await callback.answer()


@router.message(ReminderWizard.waiting_for_done_note, F.text)
async def state_done_note(
    message: Message,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    reminder_id = data.get("done_note_reminder_id")
    if not reminder_id:
        await state.clear()
        return
    note = (message.text or "").strip()
    if not note:
        await message.answer(l10n.get("done_note_prompt", "Send a short completion note."))
        return
    await reminder_dao.set_last_completion_note(int(reminder_id), note[:400])
    await state.clear()
    await message.answer(l10n.get("done_note_saved", "✅ Note saved."))


@router.callback_query(DoneSkipNextCallback.filter())
async def callback_done_skip_next(
    callback: CallbackQuery,
    callback_data: DoneSkipNextCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    if not reminder.is_recurring or not reminder.rrule_string:
        return await callback.answer(
            l10n.get("done_skip_next_failed", "❌ I couldn't skip next occurrence for this task."), show_alert=True
        )

    now_utc = datetime.now(timezone.utc)
    start_dt = reminder.execution_time
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    next_run = next_rrule_occurrence(reminder.rrule_string, start_dt, now_utc)
    if not next_run:
        return await callback.answer(
            l10n.get("done_skip_next_failed", "❌ I couldn't skip next occurrence for this task."), show_alert=True
        )

    next_run_utc_naive = to_utc_naive(next_run)
    reminder.execution_time = next_run_utc_naive
    reminder.completed_for_execution_time = None
    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None

    ok = await _schedule_reminder_safe(
        reminder_id=reminder.id,
        execution_time=next_run_utc_naive,
        is_nagging=reminder.is_nagging,
        scheduler_service=scheduler_service,
        session=reminder_dao.session,
        callback=callback,
        l10n=l10n,
    )
    if not ok:
        return
    scheduler_service.remove_nagging_job(reminder.id)

    next_str = format_time(next_run_utc_naive, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
    await callback.message.answer(
        l10n.get("done_skip_next_done", "⏭ Next occurrence skipped. New time: {time}").format(time=next_str)
    )
    await callback.answer()


@router.callback_query(DoneUndoCallback.filter())
async def callback_done_undo(
    callback: CallbackQuery,
    callback_data: DoneUndoCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    reminder.status = ReminderStatus.PENDING
    reminder.completed_at = None
    reminder.completed_for_execution_time = None
    reminder.last_completion_note = None
    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None

    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    exec_cmp = reminder.execution_time
    if exec_cmp.tzinfo is not None:
        exec_cmp = exec_cmp.astimezone(timezone.utc).replace(tzinfo=None)
    if exec_cmp <= now_utc_naive:
        reminder.execution_time = now_utc_naive + timedelta(minutes=15)

    ok = await _schedule_reminder_safe(
        reminder_id=reminder.id,
        execution_time=reminder.execution_time,
        is_nagging=reminder.is_nagging,
        scheduler_service=scheduler_service,
        session=reminder_dao.session,
        callback=callback,
        l10n=l10n,
    )
    if not ok:
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(l10n.get("done_undo_done", "↩ Done action was undone. Task is active again."))
    await callback.answer()


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

@router.callback_query(SnoozeShowCallback.filter())
async def callback_snooze_show(
    callback: CallbackQuery,
    callback_data: SnoozeShowCallback,
    l10n: dict[str, Any],
) -> None:
    await callback.message.edit_reply_markup(reply_markup=get_snooze_keyboard(callback_data.reminder_id, l10n))
    await callback.answer()


@router.callback_query(SnoozeActCallback.filter())
async def callback_snooze_act(
    callback: CallbackQuery,
    callback_data: SnoozeActCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
    if not reminder or reminder.user_id != user.id:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    action = callback_data.action

    if action == "custom":
        await state.set_state(ReminderWizard.choosing_time)
        await state.update_data(edit_reminder_id=reminder.id, text=reminder.reminder_text)
        await callback.message.edit_text(
            l10n["ask_time"].format(text=reminder.reminder_text),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n),
        )
        await callback.answer()
        return

    tz = resolve_tz(user.timezone)
    now = datetime.now(tz)

    delta_map = {
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h":  timedelta(hours=1),
        "2h":  timedelta(hours=2),
        "1d":  timedelta(days=1),
    }
    hour_map = {"morning": 9, "day": 13, "evening": 19, "night": 23}

    if action in delta_map:
        new_time = now + delta_map[action]
    elif action in hour_map:
        new_time = now.replace(hour=hour_map[action], minute=0, second=0, microsecond=0)
        if new_time <= now:
            new_time += timedelta(days=1)
    else:
        await callback.answer(l10n.get("unknown_snooze", "❌ Unknown action"), show_alert=True)
        return

    new_time_utc_naive = to_utc_naive(new_time)
    reminder.execution_time = new_time_utc_naive
    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None

    ok = await _schedule_reminder_safe(
        reminder_id=reminder.id,
        execution_time=new_time_utc_naive,
        is_nagging=reminder.is_nagging,
        scheduler_service=scheduler_service,
        session=reminder_dao.session,
        callback=callback,
        l10n=l10n,
    )
    if not ok:
        return
    scheduler_service.remove_nagging_job(reminder.id)

    friendly_time = format_time(new_time_utc_naive, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
    snooze_text = escape_markdown_v2(f"{callback.message.text}\n\n{l10n['snoozed_until'].format(time=friendly_time)}")
    await callback.message.edit_text(snooze_text, reply_markup=None, parse_mode="MarkdownV2")
    await callback.answer(l10n["snoozed_toast"])
