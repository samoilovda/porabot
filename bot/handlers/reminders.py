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
from typing import Any, Optional

import pytz
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.database.dao.habit_event import HabitEventDAO, cycle_key_for_fixed
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.database.models import is_habit_like as _is_habit_like
from bot.lexicon import ALL_MENU_BUTTON_TEXTS
from bot.keyboards.inline import (
    get_completed_tasks_keyboard,
    get_done_followup_keyboard,
    get_edit_keyboard,
    get_filtered_tasks_keyboard,
    get_parse_confirmation_keyboard,
    get_repeat_builder_keyboard,
    get_repeat_end_keyboard,
    get_repeat_weekday_keyboard,
    get_snooze_keyboard,
    get_tags_menu_keyboard,
    get_tasks_list_keyboard,
    get_time_selection_keyboard,
    get_undo_delete_keyboard,
)
from bot.services.missed_recovery import RECOVERY_DIGEST_LIMIT
from bot.services.parser import InputParser
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown, escape_markdown_v2
from bot.utils.tags import extract_tags_and_priority, format_tags, priority_glyph
from bot.utils.time_ext import format_time, next_occurrence_utc, to_utc_aware, to_utc_naive

router = Router(name="reminders")
parser = InputParser()
logger = logging.getLogger(__name__)

# asyncio.Task registry for auto-removing inline keyboards after 5 s
active_auto_delete_tasks: dict[tuple[int, int], asyncio.Task] = {}

_UNDO_DELETE_WINDOW = 5

_MENU_TEXTS = ALL_MENU_BUTTON_TEXTS
_MAX_INPUT = 3000
_NAG_LIMIT_MIN = 0
_NAG_LIMIT_MAX = 20
_COMPLETED_HISTORY_DAYS = 7
_PARSE_CONFIDENCE_THRESHOLD = 0.7
# Telegram caps inline keyboards well below 100 buttons and messages at 4096
# chars. get_tasks_list_keyboard adds up to 3 buttons per task, so an
# unbounded task list can blow both limits and the list silently fails to
# render at all. Page what's shown/rendered into buttons instead of just
# truncating with an unreachable "...and N more" tail (P1-12).
_TASKS_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RRULE_WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_RRULE_WEEKDAYS_SET = {"MO", "TU", "WE", "TH", "FR"}
_RRULE_WEEKEND_SET = {"SA", "SU"}


def _parse_rrule_parts(rrule_string: str) -> dict[str, str]:
    """Parse a flat RRULE string ("FREQ=DAILY;INTERVAL=2") into a dict.

    Deliberately not using dateutil.rrule here — we only need the raw
    key/value pairs for rendering and for reconstructing a new rule on top
    of an existing one, not date math.
    """
    parts: dict[str, str] = {}
    for chunk in (rrule_string or "").split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            parts[key.strip().upper()] = value.strip()
    return parts


def _rrule_end_label(rrule_string: Optional[str], l10n: dict[str, Any]) -> str:
    """Human-readable label for the COUNT=/UNTIL= end-condition of a rule."""
    if not rrule_string:
        return l10n.get("repeat_end_none", "unlimited")
    parts = _parse_rrule_parts(rrule_string)
    count = parts.get("COUNT")
    until = parts.get("UNTIL")
    if count:
        return l10n.get("repeat_end_count_label", "{count} times").format(count=count)
    if until:
        try:
            date_part = until[:8]
            d = datetime.strptime(date_part, "%Y%m%d").date()
            return l10n.get("repeat_end_until_label", "until {date}").format(date=d.strftime("%d.%m.%Y"))
        except ValueError:
            pass
    return l10n.get("repeat_end_none", "unlimited")


def _rrule_text(reminder, l10n: dict[str, Any]) -> str:
    """Return a human-readable recurrence label for arbitrary RRULE strings.

    Recognizes every shape the 3.1 repeat builder can produce: every-N-days,
    specific weekdays, weekdays/weekend presets, monthly-by-day, last-weekday-
    of-month, plus a COUNT=/UNTIL= end-condition suffix — not just the four
    canned patterns the old cycling button offered.
    """
    if not reminder.is_recurring or not reminder.rrule_string:
        return l10n["repeat_none"]

    rrule_string = reminder.rrule_string
    parts = _parse_rrule_parts(rrule_string)
    freq = parts.get("FREQ", "")
    try:
        interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    except ValueError:
        interval = 1
    byday = parts.get("BYDAY")
    bymonthday = parts.get("BYMONTHDAY")
    bysetpos = parts.get("BYSETPOS")

    if freq == "DAILY":
        base = l10n["repeat_day"] if interval == 1 else l10n.get("repeat_every_n_days", "Every {n} days").format(n=interval)
    elif freq == "WEEKLY":
        if byday:
            days = {d.strip() for d in byday.split(",") if d.strip()}
            if days == _RRULE_WEEKDAYS_SET:
                base = l10n["repeat_weekdays"]
            elif days == _RRULE_WEEKEND_SET:
                base = l10n.get("repeat_weekend", "Weekend")
            else:
                names = l10n.get("weekday_names") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                ordered = [c for c in _RRULE_WEEKDAY_CODES if c in days]
                base = ", ".join(
                    names[_RRULE_WEEKDAY_CODES.index(c)] for c in ordered if _RRULE_WEEKDAY_CODES.index(c) < len(names)
                ) or l10n["repeat_week"]
        else:
            base = l10n["repeat_week"] if interval == 1 else l10n.get("repeat_every_n_weeks", "Every {n} weeks").format(n=interval)
    elif freq == "MONTHLY":
        if bysetpos == "-1" and byday:
            base = l10n.get("repeat_last_weekday", "Last workday of the month")
        elif bymonthday:
            base = l10n.get("repeat_monthly_day", "Day {day} of the month").format(day=bymonthday)
        else:
            base = l10n.get("repeat_month", "Monthly")
    else:
        base = l10n["repeat_none"]

    end_label = _rrule_end_label(rrule_string, l10n)
    if end_label != l10n.get("repeat_end_none", "unlimited"):
        base = f"{base} · {end_label}"
    return base


async def _apply_repeat_change(
    reminder,
    user: User,
    scheduler_service: SchedulerService,
    reminder_dao: ReminderDAO,
    is_recurring: bool,
    rrule_string: Optional[str],
) -> bool:
    """Persist a new repeat rule and reschedule. Returns False (and rolls
    back) if scheduling fails."""
    reminder.is_recurring = is_recurring
    reminder.rrule_string = rrule_string
    await reminder_dao.session.flush()
    try:
        _reschedule_current_execution(reminder, user, scheduler_service)
    except Exception:
        await reminder_dao.session.rollback()
        return False
    return True


async def _render_repeat_builder(message: Message, reminder, l10n: dict[str, Any]) -> None:
    end_label = _rrule_end_label(reminder.rrule_string if reminder.is_recurring else None, l10n)
    await message.edit_text(
        l10n.get("repeat_builder_title", "🔁 Configure repeat:"),
        reply_markup=get_repeat_builder_keyboard(reminder.id, l10n, end_label),
    )


def _reschedule_current_execution(reminder, user: User, scheduler_service: SchedulerService) -> None:
    """(Re)schedule *reminder*'s job without firing an instant duplicate.

    Used after toggling settings (repeat/nagging) that don't change
    execution_time. If it's already in the past, advance a recurring
    reminder to its next occurrence instead of scheduling the stale
    date-job, which APScheduler would otherwise run immediately.
    """
    now = datetime.now(timezone.utc)
    run_at = to_utc_aware(reminder.execution_time)
    if run_at > now:
        scheduler_service.schedule_reminder(reminder.id, run_at, is_nagging=reminder.is_nagging)
        return

    if reminder.is_recurring and reminder.rrule_string:
        try:
            next_run_utc_naive = next_occurrence_utc(
                reminder.rrule_string, reminder.execution_time, user.timezone, now.replace(tzinfo=None)
            )
        except (ValueError, TypeError):
            next_run_utc_naive = None
        if next_run_utc_naive:
            reminder.execution_time = next_run_utc_naive
            scheduler_service.schedule_reminder(
                reminder.id, to_utc_aware(next_run_utc_naive), is_nagging=reminder.is_nagging
            )
            return

    scheduler_service.remove_reminder_job(reminder.id)


def _format_task_line_md2(task, user: User) -> str:
    """Render one task-list row for a MarkdownV2 message, escaping only the data."""
    dt_str = escape_markdown_v2(format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M"))
    flags = f"{'🔁 ' if task.is_recurring else ''}{'🔥 ' if task.is_nagging else ''}"
    # 4.3: priority glyph prefix and #tag suffix, both optional.
    glyph = priority_glyph(getattr(task, "priority", None))
    priority_prefix = f"{glyph} " if glyph else ""
    tags_text = format_tags(getattr(task, "tags", None))
    tags_suffix = f" {escape_markdown_v2(tags_text)}" if tags_text else ""
    return f"▫️ `{dt_str}`: {priority_prefix}{flags}{escape_markdown_v2(task.reminder_text)}{tags_suffix}"


def _paginate_tasks_for_list(tasks: list, page: int = 0) -> tuple[list, int, int]:
    """Slice *tasks* (already in a stable order — see get_user_reminders'
    (execution_time, id) ordering) into pages of _TASKS_PAGE_SIZE, both so
    the message text and its per-task keyboard buttons stay within
    Telegram's limits, and so tasks beyond the first page are actually
    reachable (P1-12) instead of just listed as "...and N more".

    *page* is clamped into range. Returns (shown_tasks, clamped_page,
    total_pages) — total_pages is always >= 1.
    """
    total_pages = max(1, (len(tasks) + _TASKS_PAGE_SIZE - 1) // _TASKS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _TASKS_PAGE_SIZE
    shown = tasks[start:start + _TASKS_PAGE_SIZE]
    return shown, page, total_pages


def _render_tasks_list_text(shown_tasks: list, user: User, l10n: dict[str, Any], page: int, total_pages: int) -> str:
    lines = [l10n["tasks_header"]] + [_format_task_line_md2(task, user) for task in shown_tasks]
    if total_pages > 1:
        lines.append(l10n.get("tasks_page_indicator", "📄 {page}/{total}").format(page=page + 1, total=total_pages))
    return "\n".join(lines)


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


# NOTE: the actual hard-delete is NOT driven by an in-memory asyncio.Task
# any more — that made deletion invisible to a process restart mid-window:
# the DB row stayed a completely normal status='pending' reminder with its
# scheduler job already removed, so reconcile_jobs_with_db would create a
# fresh job for it on the next startup, resurrecting a task the user just
# deleted. Instead, `reminder.pending_delete_at` is set and committed
# synchronously (see callback_edit_delete/callback_delete_task below) —
# every query that lists, schedules, sweeps, or recovers reminders excludes
# rows with it set — and bot/services/delete_cleanup.py's minutely sweep
# hard-deletes them once the deadline passes, restart or not.


def _reset_auto_delete(message: Message) -> None:
    """Cancel the pending keyboard-removal task for *message* (if any)."""
    task = active_auto_delete_tasks.get(_message_task_key(message))
    if task and not task.done():
        task.cancel()


def _cleanup_stale_timers() -> None:
    """Drop finished tasks from active_auto_delete_tasks (W5).

    _remove_keyboard_after_delay already pops its own entry in a finally
    block, but a task cancelled via _reset_auto_delete before it starts
    sleeping, or one that errors before reaching the finally, can leave a
    stale reference behind. Registered as a periodic job in bot/__main__.py
    so the dict can't grow unbounded over a long-running process.
    """
    for key, task in list(active_auto_delete_tasks.items()):
        if task.done():
            active_auto_delete_tasks.pop(key, None)


def _format_parse_confidence(confidence: float) -> int:
    return max(0, min(100, int(round(confidence * 100))))


def _pick_done_reply(l10n: dict[str, Any]) -> str:
    """Return a randomized done-reply phrase with compatibility fallback."""
    options = l10n.get("task_done_replies")
    if isinstance(options, list):
        normalized = [str(item) for item in options if item]
        if normalized:
            return random.choice(normalized)
    return str(l10n.get("task_done_reply", "✅ *Great\\!*"))


async def _handle_parsed_result(
    source_message: Message,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
    result,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    # 4.3: pull #tags and !priority out of the phrase before anything else
    # touches clean_text — everything downstream (confirmation prompt,
    # preview, stored reminder_text) should see the phrase without them.
    raw_text = result.clean_text or l10n.get("task_untitled", "Untitled task")
    clean_text, tags_csv, priority = extract_tags_and_priority(raw_text)
    clean_text = clean_text or l10n.get("task_untitled", "Untitled task")
    await state.update_data(
        text=clean_text, tags=tags_csv, priority=priority,
        user_timezone=user.timezone, chat_id=source_message.chat.id,
    )

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
                    text=escape_markdown(clean_text),
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
        l10n["ask_time"].format(text=escape_markdown(clean_text)),
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
    tags_csv = data.get("tags")
    priority = data.get("priority")
    execution_time_raw = datetime.fromisoformat(data["execution_time"])
    execution_time = to_utc_naive(execution_time_raw)
    edit_reminder_id = data.get("edit_reminder_id")

    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if execution_time <= now_utc_naive + timedelta(minutes=1):
        await state.set_state(ReminderWizard.choosing_time)
        await source_message.answer(
            l10n.get("time_in_past", "⏰ This time has already passed. Please choose a future time."),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset),
        )
        return

    if edit_reminder_id:
        new_reminder = await reminder_dao.get_owned(edit_reminder_id, user.id)
        if new_reminder:
            new_reminder.reminder_text = text
            new_reminder.tags = tags_csv
            new_reminder.priority = priority
            is_snooze_mode = bool(data.get("is_snooze_mode", False))
            if not (is_snooze_mode and _is_habit_like(new_reminder) and new_reminder.is_recurring):
                new_reminder.execution_time = execution_time
        else:
            logger.warning("Reminder %s not found during edit.", edit_reminder_id)
            return
    else:
        try:
            new_reminder = await reminder_dao.create_reminder(
                user_id=user.id,
                text=text,
                execution_time=execution_time,
                is_recurring=False,
                rrule_string=None,
                is_nagging=False,
                tags=tags_csv,
                priority=priority,
            )
        except ValueError as ve:
            logger.warning("Validation error for user %s: %s", user.id, ve)
            await source_message.answer(str(ve))
            await state.clear()
            return

    try:
        # For habit-like recurring snooze, new_reminder.execution_time is left
        # untouched above (anti-drift guard), but the job itself must still
        # fire at the time the user picked — schedule from `execution_time`,
        # not the (possibly stale) DB field.
        scheduler_service.schedule_reminder(
            new_reminder.id,
            to_utc_aware(execution_time),
            is_nagging=new_reminder.is_nagging,
        )
    except Exception as e:
        logger.error("Failed to schedule reminder %s: %s", new_reminder.id, e, exc_info=True)
        # Critical: rollback DAO changes when scheduling failed, otherwise reminder
        # would be committed but never executed.
        await reminder_dao.session.rollback()
        await source_message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        await state.clear()
        return

    await state.clear()

    date_str = format_time(execution_time, user.timezone, user.show_utc_offset, "%d.%m.%Y %H:%M")
    safe_preview = l10n["preview"].format(
        text=escape_markdown_v2(new_reminder.reminder_text),
        time=escape_markdown_v2(date_str),
    )
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
# FSM: text input
# ---------------------------------------------------------------------------
# NOTE: the "New Task" / "My Tasks" main-menu button handlers live in
# bot/handlers/menu.py (registered on an earlier router) so they can't be
# swallowed by another router's stateful FSM handlers. _format_task_line_md2
# below is still used here by callback_refresh_tasks and imported from there
# by menu.py's btn_my_tasks.

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
            origin_name = getattr(fwd, "sender_chat").title if getattr(fwd, "sender_chat", None) else "Group"

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
        execution_time = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
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


# ---------------------------------------------------------------------------
# Repeat (RRULE) builder — 3.1
# ---------------------------------------------------------------------------
# Replaces the old edit_toggle_repeat_ cycling button with a real builder UI
# over next_occurrence_utc's existing rrulestr engine (bot/utils/time_ext.py).
# All frequency-preset callbacks below reset the end-condition (COUNT=/
# UNTIL=) to keep merging simple: pick a base rule first, then optionally
# open "⏳ End: ..." to layer a COUNT= or UNTIL= on top of it.

async def _get_owned_or_alert(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any], prefix: str):
    reminder_id = int(callback.data.split(prefix)[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return None
    return reminder


@router.callback_query(F.data.startswith("edit_repeat_menu_"))
async def callback_edit_repeat_menu(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "edit_repeat_menu_")
    if not reminder:
        return
    await _render_repeat_builder(callback.message, reminder, l10n)
    await callback.answer()


@router.callback_query(F.data.startswith("rrb_open_"))
async def callback_rrb_open(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_open_")
    if not reminder:
        return
    await _render_repeat_builder(callback.message, reminder, l10n)
    await callback.answer()


async def _apply_and_refresh(
    callback: CallbackQuery,
    reminder,
    user: User,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    is_recurring: bool,
    rrule_string: Optional[str],
) -> None:
    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, is_recurring, rrule_string)
    if not ok:
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
        return
    await _render_repeat_builder(callback.message, reminder, l10n)
    await callback.answer(l10n.get("repeat_saved", "✅ Repeat updated."))


@router.callback_query(F.data.startswith("rrb_none_"))
async def callback_rrb_none(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_none_")
    if not reminder:
        return
    await _apply_and_refresh(callback, reminder, user, l10n, reminder_dao, scheduler_service, False, None)


@router.callback_query(F.data.startswith("rrb_daily_"))
async def callback_rrb_daily(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_daily_")
    if not reminder:
        return
    await _apply_and_refresh(callback, reminder, user, l10n, reminder_dao, scheduler_service, True, "FREQ=DAILY")


@router.callback_query(F.data.startswith("rrb_weekdays_"))
async def callback_rrb_weekdays(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_weekdays_")
    if not reminder:
        return
    await _apply_and_refresh(
        callback, reminder, user, l10n, reminder_dao, scheduler_service, True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    )


@router.callback_query(F.data.startswith("rrb_weekend_"))
async def callback_rrb_weekend(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_weekend_")
    if not reminder:
        return
    await _apply_and_refresh(
        callback, reminder, user, l10n, reminder_dao, scheduler_service, True, "FREQ=WEEKLY;BYDAY=SA,SU"
    )


@router.callback_query(F.data.startswith("rrb_weekly_"))
async def callback_rrb_weekly(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_weekly_")
    if not reminder:
        return
    await _apply_and_refresh(callback, reminder, user, l10n, reminder_dao, scheduler_service, True, "FREQ=WEEKLY")


@router.callback_query(F.data.startswith("rrb_lastwd_"))
async def callback_rrb_last_weekday(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_lastwd_")
    if not reminder:
        return
    await _apply_and_refresh(
        callback,
        reminder,
        user,
        l10n,
        reminder_dao,
        scheduler_service,
        True,
        "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1",
    )


@router.callback_query(F.data.startswith("rrb_interval_"))
async def callback_rrb_interval_prompt(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_interval_")
    if not reminder:
        return
    await state.set_state(ReminderWizard.waiting_for_repeat_interval)
    await state.update_data(rrb_reminder_id=reminder.id)
    await callback.message.edit_text(l10n.get("repeat_interval_prompt", "Every how many days? Send a number (e.g. 3)."))
    await callback.answer()


@router.message(ReminderWizard.waiting_for_repeat_interval, F.text)
async def state_rrb_interval(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any],
) -> None:
    try:
        n = int((message.text or "").strip())
    except ValueError:
        n = None
    if n is None or n < 1 or n > 365:
        await message.answer(l10n.get("repeat_interval_invalid", "❌ Send a whole number from 1 to 365."))
        return
    data = await state.get_data()
    reminder = await reminder_dao.get_owned(int(data.get("rrb_reminder_id", 0)), user.id)
    await state.clear()
    if not reminder:
        await message.answer(l10n["item_not_found"])
        return
    rrule = "FREQ=DAILY" if n == 1 else f"FREQ=DAILY;INTERVAL={n}"
    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, True, rrule)
    if not ok:
        await message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        return
    end_label = _rrule_end_label(reminder.rrule_string, l10n)
    await message.answer(
        l10n.get("repeat_saved", "✅ Repeat updated."),
        reply_markup=get_repeat_builder_keyboard(reminder.id, l10n, end_label),
    )


@router.callback_query(F.data.startswith("rrb_monthly_"))
async def callback_rrb_monthly_prompt(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_monthly_")
    if not reminder:
        return
    await state.set_state(ReminderWizard.waiting_for_repeat_monthday)
    await state.update_data(rrb_reminder_id=reminder.id)
    await callback.message.edit_text(l10n.get("repeat_monthday_prompt", "Which day of the month? Send a number from 1 to 28."))
    await callback.answer()


@router.message(ReminderWizard.waiting_for_repeat_monthday, F.text)
async def state_rrb_monthday(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any],
) -> None:
    try:
        n = int((message.text or "").strip())
    except ValueError:
        n = None
    if n is None or n < 1 or n > 28:
        await message.answer(l10n.get("repeat_monthday_invalid", "❌ Send a number from 1 to 28."))
        return
    data = await state.get_data()
    reminder = await reminder_dao.get_owned(int(data.get("rrb_reminder_id", 0)), user.id)
    await state.clear()
    if not reminder:
        await message.answer(l10n["item_not_found"])
        return
    rrule = f"FREQ=MONTHLY;BYMONTHDAY={n}"
    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, True, rrule)
    if not ok:
        await message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        return
    end_label = _rrule_end_label(reminder.rrule_string, l10n)
    await message.answer(
        l10n.get("repeat_saved", "✅ Repeat updated."),
        reply_markup=get_repeat_builder_keyboard(reminder.id, l10n, end_label),
    )


@router.callback_query(F.data.startswith("rrb_customdays_"))
async def callback_rrb_customdays_open(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_customdays_")
    if not reminder:
        return
    parts = _parse_rrule_parts(reminder.rrule_string or "")
    preselected = {d for d in (parts.get("BYDAY", "").split(",")) if d}
    await state.update_data(rrb_selected_days=sorted(preselected))
    await callback.message.edit_text(
        l10n.get("repeat_weekday_pick_title", "Pick the days of the week:"),
        reply_markup=get_repeat_weekday_keyboard(reminder.id, l10n, preselected),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rrb_wd_"))
async def callback_rrb_toggle_weekday(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    payload = callback.data[len("rrb_wd_"):]
    try:
        reminder_id_raw, day_code = payload.rsplit("_", 1)
        reminder_id = int(reminder_id_raw)
    except ValueError:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("rrb_selected_days") or [])
    if day_code in selected:
        selected.discard(day_code)
    else:
        selected.add(day_code)
    await state.update_data(rrb_selected_days=sorted(selected))
    await callback.message.edit_reply_markup(reply_markup=get_repeat_weekday_keyboard(reminder.id, l10n, selected))
    await callback.answer()


@router.callback_query(F.data.startswith("rrb_wddone_"))
async def callback_rrb_weekday_done(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any],
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_wddone_")
    if not reminder:
        return
    data = await state.get_data()
    selected = [c for c in _RRULE_WEEKDAY_CODES if c in set(data.get("rrb_selected_days") or [])]
    await state.update_data(rrb_selected_days=None)
    if not selected:
        await callback.answer(l10n.get("repeat_weekday_pick_empty", "❌ Pick at least one day."), show_alert=True)
        return
    rrule = f"FREQ=WEEKLY;BYDAY={','.join(selected)}"
    await _apply_and_refresh(callback, reminder, user, l10n, reminder_dao, scheduler_service, True, rrule)


@router.callback_query(F.data.startswith("rrb_end_"))
async def callback_rrb_end_menu(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_end_")
    if not reminder:
        return
    await callback.message.edit_text(
        l10n.get("repeat_builder_title", "🔁 Configure repeat:"),
        reply_markup=get_repeat_end_keyboard(reminder.id, l10n),
    )
    await callback.answer()


def _strip_end_condition(rrule_string: Optional[str]) -> str:
    parts = _parse_rrule_parts(rrule_string or "")
    parts.pop("COUNT", None)
    parts.pop("UNTIL", None)
    if not parts.get("FREQ"):
        parts["FREQ"] = "DAILY"
    return ";".join(f"{k}={v}" for k, v in parts.items())


@router.callback_query(F.data.startswith("rrb_endnone_"))
async def callback_rrb_end_none(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_endnone_")
    if not reminder:
        return
    rrule = _strip_end_condition(reminder.rrule_string)
    await _apply_and_refresh(callback, reminder, user, l10n, reminder_dao, scheduler_service, True, rrule)


@router.callback_query(F.data.startswith("rrb_endcount_"))
async def callback_rrb_endcount_prompt(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_endcount_")
    if not reminder:
        return
    await state.set_state(ReminderWizard.waiting_for_repeat_end_count)
    await state.update_data(rrb_reminder_id=reminder.id)
    await callback.message.edit_text(l10n.get("repeat_end_count_prompt", "Stop after how many repeats? Send a number from 1 to 999."))
    await callback.answer()


@router.message(ReminderWizard.waiting_for_repeat_end_count, F.text)
async def state_rrb_end_count(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any],
) -> None:
    try:
        n = int((message.text or "").strip())
    except ValueError:
        n = None
    if n is None or n < 1 or n > 999:
        await message.answer(l10n.get("repeat_end_count_invalid", "❌ Send a number from 1 to 999."))
        return
    data = await state.get_data()
    reminder = await reminder_dao.get_owned(int(data.get("rrb_reminder_id", 0)), user.id)
    await state.clear()
    if not reminder or not reminder.is_recurring or not reminder.rrule_string:
        await message.answer(l10n["item_not_found"])
        return
    base = _strip_end_condition(reminder.rrule_string)
    rrule = f"{base};COUNT={n}"
    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, True, rrule)
    if not ok:
        await message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        return
    end_label = _rrule_end_label(reminder.rrule_string, l10n)
    await message.answer(
        l10n.get("repeat_saved", "✅ Repeat updated."),
        reply_markup=get_repeat_builder_keyboard(reminder.id, l10n, end_label),
    )


@router.callback_query(F.data.startswith("rrb_enduntil_"))
async def callback_rrb_enduntil_prompt(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_enduntil_")
    if not reminder:
        return
    await state.set_state(ReminderWizard.waiting_for_repeat_end_until)
    await state.update_data(rrb_reminder_id=reminder.id)
    await callback.message.edit_text(l10n.get("repeat_end_date_prompt", "Repeat until which date? Send DD.MM.YYYY."))
    await callback.answer()


@router.message(ReminderWizard.waiting_for_repeat_end_until, F.text)
async def state_rrb_end_until(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any],
) -> None:
    raw = (message.text or "").strip()
    parsed_date = None
    try:
        parsed_date = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        pass
    today_local = datetime.now(timezone.utc).date()
    if parsed_date is None or parsed_date <= today_local:
        await message.answer(l10n.get("repeat_end_date_invalid", "❌ Send a future date as DD.MM.YYYY."))
        return
    data = await state.get_data()
    reminder = await reminder_dao.get_owned(int(data.get("rrb_reminder_id", 0)), user.id)
    await state.clear()
    if not reminder or not reminder.is_recurring or not reminder.rrule_string:
        await message.answer(l10n["item_not_found"])
        return
    base = _strip_end_condition(reminder.rrule_string)
    rrule = f"{base};UNTIL={parsed_date.strftime('%Y%m%d')}"
    ok = await _apply_repeat_change(reminder, user, scheduler_service, reminder_dao, True, rrule)
    if not ok:
        await message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        return
    end_label = _rrule_end_label(reminder.rrule_string, l10n)
    await message.answer(
        l10n.get("repeat_saved", "✅ Repeat updated."),
        reply_markup=get_repeat_builder_keyboard(reminder.id, l10n, end_label),
    )


@router.callback_query(F.data.startswith("rrb_back_"))
async def callback_rrb_back(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder = await _get_owned_or_alert(callback, reminder_dao, user, l10n, "rrb_back_")
    if not reminder:
        return
    await callback.message.edit_text(
        l10n["task_settings_title"].format(text=escape_markdown(reminder.reminder_text)),
        reply_markup=get_edit_keyboard(
            reminder.id,
            l10n,
            reminder.is_recurring,
            reminder.is_nagging,
            reminder.nagging_max_repeats,
            _rrule_text(reminder, l10n),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_toggle_nagging_"))
async def callback_edit_nagging(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    user: User, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_toggle_nagging_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    reminder.is_nagging = not reminder.is_nagging
    try:
        _reschedule_current_execution(reminder, user, scheduler_service)
        if not reminder.is_nagging:
            reminder.nagging_sent_count = 0
            reminder.last_nag_chat_id = None
            reminder.last_nag_message_id = None
            scheduler_service.remove_nagging_job(reminder.id)
    except Exception:
        await reminder_dao.session.rollback()
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
        return

    await callback.message.edit_reply_markup(
        reply_markup=get_edit_keyboard(
            reminder.id,
            l10n,
            reminder.is_recurring,
            reminder.is_nagging,
            reminder.nagging_max_repeats,
            _rrule_text(reminder, l10n),
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_delete_"))
async def callback_edit_delete(
    callback: CallbackQuery, reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_delete_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    # Stop the job immediately; the DB row itself is only removed once the
    # undo window elapses (see delete_cleanup.py), so an Undo tap can still
    # restore it without recreating the reminder.
    scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)
    # Persisted and committed before confirming to the user — a restart
    # right after this is still durable (every active-reminder query
    # excludes pending_delete_at rows), unlike the old in-memory timer.
    reminder.pending_delete_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=_UNDO_DELETE_WINDOW
    )
    await reminder_dao.session.commit()
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(
        l10n["task_deleted"], reply_markup=get_undo_delete_keyboard(reminder_id, l10n)
    )
    task = asyncio.create_task(_remove_keyboard_after_delay(callback.message, _UNDO_DELETE_WINDOW))
    active_auto_delete_tasks[_message_task_key(callback.message)] = task


# ---------------------------------------------------------------------------
# Task list callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("task_settings_"))
async def callback_task_settings(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    reminder_id = int(callback.data.split("task_settings_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    await callback.message.answer(
        l10n["task_settings_title"].format(text=escape_markdown(reminder.reminder_text)),
        reply_markup=get_edit_keyboard(
            reminder.id,
            l10n,
            reminder.is_recurring,
            reminder.is_nagging,
            reminder.nagging_max_repeats,
            _rrule_text(reminder, l10n),
        ),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("del_task_"))
async def callback_delete_task(
    callback: CallbackQuery, reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    task_id = int(callback.data.split("del_task_")[1])
    reminder = await reminder_dao.get_owned(task_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    # Stop the job immediately; the DB row (and any habit_events, for fixed
    # habits reachable from "My Tasks") is only removed once the undo window
    # elapses (see delete_cleanup.py), so an Undo tap can still restore it.
    scheduler_service.remove_reminder_job(task_id)
    scheduler_service.remove_nagging_job(task_id)
    # Persisted and committed before confirming to the user — see
    # callback_edit_delete above for why this must not be an in-memory timer.
    reminder.pending_delete_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=_UNDO_DELETE_WINDOW
    )
    await reminder_dao.session.commit()
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(
        l10n["task_deleted"], reply_markup=get_undo_delete_keyboard(task_id, l10n)
    )
    task = asyncio.create_task(_remove_keyboard_after_delay(callback.message, _UNDO_DELETE_WINDOW))
    active_auto_delete_tasks[_message_task_key(callback.message)] = task


@router.callback_query(F.data.startswith("undo_del_"))
async def callback_undo_delete(
    callback: CallbackQuery, reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService, user: User, l10n: dict[str, Any]
) -> None:
    reminder_id = int(callback.data.split("undo_del_")[1])

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if reminder.pending_delete_at is None or reminder.pending_delete_at <= now:
        # Never deleted, already restored, or the cleanup sweep in
        # delete_cleanup.py is at/past this deadline — too late to undo.
        return await callback.answer(l10n["undo_too_late"], show_alert=True)

    _reset_auto_delete(callback.message)
    reminder.pending_delete_at = None
    await reminder_dao.session.flush()

    try:
        _reschedule_current_execution(reminder, user, scheduler_service)
    except Exception:
        await reminder_dao.session.rollback()
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
        return

    date_str = format_time(reminder.execution_time, user.timezone, user.show_utc_offset, "%d.%m.%Y %H:%M")
    safe_preview = l10n["preview"].format(
        text=escape_markdown_v2(reminder.reminder_text),
        time=escape_markdown_v2(date_str),
    )
    await callback.message.edit_text(
        safe_preview,
        reply_markup=get_edit_keyboard(
            reminder.id,
            l10n,
            reminder.is_recurring,
            reminder.is_nagging,
            reminder.nagging_max_repeats,
            _rrule_text(reminder, l10n),
        ),
        parse_mode="MarkdownV2",
    )
    await callback.answer(l10n["task_restored"])


@router.callback_query(F.data == "close_tasks")
async def callback_close_tasks(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.delete()


@router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery) -> None:
    """The page-indicator button in get_tasks_list_keyboard — not clickable
    in any meaningful sense, just needs SOME callback_data."""
    await callback.answer()


@router.callback_query(F.data.startswith("tasks_page_"))
async def callback_tasks_page(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    """Render a specific page of "My Tasks" — also doubles as Refresh
    (which points at the CURRENT page, see get_tasks_list_keyboard) so both
    stay on the same page instead of bouncing back to the first one."""
    try:
        requested_page = int(callback.data[len("tasks_page_"):])
    except ValueError:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await callback.message.edit_text(l10n["no_tasks"], reply_markup=None)
        await callback.answer()
        return

    shown_tasks, page, total_pages = _paginate_tasks_for_list(tasks, page=requested_page)
    safe_text = _render_tasks_list_text(shown_tasks, user, l10n, page, total_pages)
    await callback.message.edit_text(
        safe_text,
        reply_markup=get_tasks_list_keyboard(shown_tasks, l10n, page=page, total_pages=total_pages),
        parse_mode="MarkdownV2",
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Search and filters — 3.4
# ---------------------------------------------------------------------------

def _render_filtered_tasks_text(tasks: list, user: User, l10n: dict[str, Any], header: str) -> str:
    lines = [header] + [_format_task_line_md2(task, user) for task in tasks[:_TASKS_PAGE_SIZE]]
    return "\n".join(lines)


@router.message(Command("find"))
async def cmd_find(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    await state.clear()
    query = message.text.split(maxsplit=1)[1].strip() if message.text and " " in message.text else ""
    if not query:
        await message.answer(l10n.get("find_usage", "Usage: /find <text>"))
        return

    tasks = await reminder_dao.search_user_reminders(user.id, query)
    if not tasks:
        await message.answer(l10n.get("find_no_results", "🔍 Nothing found for «{query}».").format(query=escape_markdown(query)))
        return

    header = l10n.get("find_results_header", "🔍 *Results for «{query}»:*\n").format(query=escape_markdown_v2(query))
    await message.answer(
        _render_filtered_tasks_text(tasks, user, l10n, header),
        reply_markup=get_filtered_tasks_keyboard(tasks, l10n),
        parse_mode="MarkdownV2",
    )


async def _show_filtered_tasks(
    callback: CallbackQuery, tasks: list, user: User, l10n: dict[str, Any], header_key: str, header_default: str
) -> None:
    if not tasks:
        await callback.message.edit_text(l10n.get("find_no_results_filter", "🔍 No tasks match this filter."), reply_markup=None)
        await callback.answer()
        return
    header = l10n.get(header_key, header_default)
    await callback.message.edit_text(
        _render_filtered_tasks_text(tasks, user, l10n, header),
        reply_markup=get_filtered_tasks_keyboard(tasks, l10n),
        parse_mode="MarkdownV2",
    )
    await callback.answer()


@router.callback_query(F.data == "tasks_filter_today")
async def callback_tasks_filter_today(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders_today(user.id, user.timezone)
    await _show_filtered_tasks(callback, tasks, user, l10n, "filter_header_today", "📅 *Today:*\n")


@router.callback_query(F.data == "tasks_filter_week")
async def callback_tasks_filter_week(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders_this_week(user.id, user.timezone)
    await _show_filtered_tasks(callback, tasks, user, l10n, "filter_header_week", "🗓 *This week:*\n")


@router.callback_query(F.data == "tasks_filter_overdue")
async def callback_tasks_filter_overdue(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=0)
    await _show_filtered_tasks(callback, tasks, user, l10n, "filter_header_overdue", "⏰ *Overdue:*\n")


@router.callback_query(F.data == "tasks_filter_recurring")
async def callback_tasks_filter_recurring(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders_recurring(user.id)
    await _show_filtered_tasks(callback, tasks, user, l10n, "filter_header_recurring", "🔁 *Recurring:*\n")


@router.callback_query(F.data == "tasks_tags_menu")
async def callback_tasks_tags_menu(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    """4.3: list the user's distinct tags as buttons to filter by."""
    tags = await reminder_dao.get_distinct_tags(user.id)
    if not tags:
        await callback.answer(l10n.get("no_tags_yet", "You have no tags yet."), show_alert=True)
        return
    await callback.message.edit_text(
        l10n.get("tags_menu_title", "🏷 Pick a tag:"),
        reply_markup=get_tags_menu_keyboard(list(tags), l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tasks_tag:"))
async def callback_tasks_filter_by_tag(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    """4.3: results for one tag, tapped from callback_tasks_tags_menu."""
    tag = callback.data.split("tasks_tag:", 1)[1]
    tasks = await reminder_dao.get_reminders_by_tag(user.id, tag)
    if not tasks:
        await callback.message.edit_text(l10n.get("find_no_results_filter", "🔍 No tasks match this filter."), reply_markup=None)
        await callback.answer()
        return
    header = l10n.get("filter_header_tag", "🏷 *#{tag}:*\n").format(tag=escape_markdown_v2(tag))
    await callback.message.edit_text(
        _render_filtered_tasks_text(tasks, user, l10n, header),
        reply_markup=get_filtered_tasks_keyboard(tasks, l10n),
        parse_mode="MarkdownV2",
    )
    await callback.answer()


@router.callback_query(F.data == "recovery_done_all")
async def callback_recovery_done_all(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    overdue = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=RECOVERY_DIGEST_LIMIT)
    if not overdue:
        await callback.answer(l10n.get("no_tasks", "No tasks"), show_alert=True)
        return

    now_utc = datetime.now(timezone.utc)
    for task in overdue:
        if _is_habit_like(task):
            due_at = task.habit_active_due_at or task.execution_time
            if due_at is not None:
                streak_result = await reminder_dao.apply_habit_streak_completion(
                    task.id,
                    due_at_utc_naive=due_at,
                    completed_at_utc_naive=now_utc.replace(tzinfo=None),
                )
                # P1-5: without this, weekly/monthly habit reports (built
                # from habit_events, not the streak counters) never see a
                # habit completed via this bulk "Done all" recovery action.
                if not streak_result.get("already_counted"):
                    await habit_event_dao.record(
                        reminder=task,
                        user_tz=user.timezone,
                        outcome="done",
                        source="recovery",
                        due_at_utc_naive=due_at,
                    )
        await reminder_dao.mark_done(task.id)
        if task.is_recurring and task.rrule_string:
            try:
                next_run_utc_naive = next_occurrence_utc(
                    task.rrule_string, task.execution_time, user.timezone, now_utc.replace(tzinfo=None)
                )
            except Exception:
                next_run_utc_naive = None

            if next_run_utc_naive:
                task.execution_time = next_run_utc_naive
                task.completed_for_execution_time = None
                try:
                    scheduler_service.schedule_reminder(
                        task.id,
                        to_utc_aware(next_run_utc_naive),
                        is_nagging=task.is_nagging,
                    )
                except Exception:
                    await reminder_dao.session.rollback()
                    return await callback.answer(
                        l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again."),
                        show_alert=True,
                    )
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
    overdue = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=RECOVERY_DIGEST_LIMIT)
    if not overdue:
        await callback.answer(l10n.get("no_tasks", "No tasks"), show_alert=True)
        return

    new_time = datetime.now(pytz.UTC).replace(tzinfo=None) + timedelta(hours=1)
    for task in overdue:
        # For habit-like recurring reminders, do not overwrite execution_time —
        # it anchors the rrule so the next day's occurrence stays on the correct
        # original time. Only reschedule the current job.
        if not (_is_habit_like(task) and task.is_recurring):
            task.execution_time = new_time
        task.completed_for_execution_time = None
        task.last_nag_chat_id = None
        task.last_nag_message_id = None
        try:
            scheduler_service.schedule_reminder(task.id, new_time, is_nagging=task.is_nagging)
            scheduler_service.remove_nagging_job(task.id)
        except Exception:
            await reminder_dao.session.rollback()
            return await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again."), show_alert=True)

    await callback.message.edit_text(
        l10n.get("recovery_snooze_all_done", "⏰ Snoozed {count} overdue tasks by 1 hour.").format(count=len(overdue)),
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_set_nag_limit_"))
async def callback_edit_set_nag_limit(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_set_nag_limit_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
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
    user: User,
    l10n: dict[str, Any],
) -> None:
    raw_value = message.text.strip() if message.text else ""
    try:
        nag_limit = int(raw_value)
    except ValueError:
        await message.answer(
            l10n["nagging_limit_invalid"].format(min=_NAG_LIMIT_MIN, max=_NAG_LIMIT_MAX)
        )
        return

    if nag_limit < _NAG_LIMIT_MIN or nag_limit > _NAG_LIMIT_MAX:
        await message.answer(
            l10n["nagging_limit_invalid"].format(min=_NAG_LIMIT_MIN, max=_NAG_LIMIT_MAX)
        )
        return

    state_data = await state.get_data()
    reminder_id = state_data.get("nag_limit_reminder_id")
    if not reminder_id:
        await state.clear()
        await message.answer(l10n.get("parse_error", "Error parsing text. Check the format."))
        return

    reminder = await reminder_dao.get_owned(int(reminder_id), user.id)
    if not reminder:
        await state.clear()
        await message.answer(l10n["item_not_found"])
        return

    reminder.nagging_max_repeats = nag_limit
    reminder.nagging_sent_count = min(max(0, int(reminder.nagging_sent_count)), nag_limit)
    if (
        not reminder.is_nagging
        or nag_limit == 0
        or reminder.nagging_sent_count >= nag_limit
    ):
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None
        scheduler_service.remove_nagging_job(reminder.id)
    else:
        # Raising the limit above a previously-exhausted chain leaves it
        # dead until the next main fire unless we explicitly resume it.
        scheduler_service.resume_nagging_if_stalled(reminder)

    await state.clear()
    await message.answer(
        l10n["nagging_limit_updated"].format(count=nag_limit),
        reply_markup=get_edit_keyboard(
            reminder.id,
            l10n,
            reminder.is_recurring,
            reminder.is_nagging,
            reminder.nagging_max_repeats,
            _rrule_text(reminder, l10n),
        ),
    )


@router.callback_query(F.data == "show_completed")
async def callback_show_completed(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    completed = await reminder_dao.get_recent_completed_tasks(
        user.id,
        user.timezone,
        days=_COMPLETED_HISTORY_DAYS,
    )
    if not completed:
        await callback.answer(l10n["no_completed_tasks"], show_alert=True)
        return

    lines = [l10n["completed_header"]]
    for task in completed:
        completed_dt = task.completed_at or task.execution_time
        dt_str = escape_markdown_v2(format_time(completed_dt, user.timezone, user.show_utc_offset, "%d.%m %H:%M"))
        note_suffix = f" — {escape_markdown_v2(task.last_completion_note)}" if task.last_completion_note else ""
        lines.append(f"✅ `{dt_str}`: ~{escape_markdown_v2(task.reminder_text)}~{note_suffix}")

    safe_text = "\n".join(lines)
    await callback.message.edit_text(safe_text, reply_markup=get_completed_tasks_keyboard(l10n), parse_mode="MarkdownV2")
    await callback.answer()


# ---------------------------------------------------------------------------
# Mark done
# ---------------------------------------------------------------------------

def _replace_wrapup_row(
    reply_markup: InlineKeyboardMarkup | None,
    *,
    callback_data: str,
    status_text: str,
) -> InlineKeyboardMarkup | None:
    """Replace one evening-wrap-up action row with its selected status."""
    if not reply_markup:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for row in reply_markup.inline_keyboard:
        if any(button.callback_data == callback_data for button in row):
            task_button = row[0] if row else None
            if task_button:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=task_button.text,
                            callback_data=task_button.callback_data or "wrap_task",
                        ),
                        InlineKeyboardButton(text=status_text, callback_data="wrap_selected"),
                    ]
                )
            continue
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _mark_wrapup_task_done(
    reminder_id: int,
    *,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user_id: int,
    user_tz: str,
) -> bool:
    """Mark a wrap-up task done without rewriting the whole summary message."""
    reminder = await reminder_dao.get_owned(reminder_id, user_id)
    if (
        not reminder
        or reminder.status == "completed"
        or (
            reminder.is_recurring
            and reminder.completed_for_execution_time is not None
            and reminder.completed_for_execution_time >= reminder.execution_time
        )
    ):
        return False

    if _is_habit_like(reminder):
        due_at = reminder.habit_active_due_at or reminder.execution_time
        if due_at is not None:
            streak_result = await reminder_dao.apply_habit_streak_completion(
                reminder.id,
                due_at_utc_naive=due_at,
                completed_at_utc_naive=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            if streak_result.get("already_counted"):
                return False
            await habit_event_dao.record(
                reminder=reminder,
                user_tz=user_tz,
                outcome="done",
                source="wrapup",
                due_at_utc_naive=due_at,
            )

    await reminder_dao.mark_done(reminder_id)
    if not reminder.is_recurring:
        scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)
    return True


@router.callback_query(F.data.startswith("wrap_task_"))
async def callback_wrapup_task_label(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.answer(l10n.get("wrapup_task_hint", "Task from evening wrap-up"))


@router.callback_query(F.data == "wrap_selected")
async def callback_wrapup_selected(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("wrap_done_"))
async def callback_wrapup_done(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    try:
        reminder_id = int(callback.data.split("wrap_done_")[1])
    except (IndexError, ValueError):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    marked = await _mark_wrapup_task_done(
        reminder_id,
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=scheduler_service,
        user_id=user.id,
        user_tz=user.timezone,
    )
    status_text = l10n.get("btn_done_short", "Done")
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_replace_wrapup_row(
                callback.message.reply_markup,
                callback_data=callback.data,
                status_text=status_text,
            )
        )
    except TelegramBadRequest:
        pass
    await callback.answer(
        l10n.get("wrapup_done_saved", "✅ Marked done for tonight.")
        if marked
        else l10n.get("already_done", "Already done ✅")
    )


@router.callback_query(F.data.startswith("wrap_not_done_"))
async def callback_wrapup_not_done(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    try:
        reminder_id = int(callback.data.split("wrap_not_done_")[1])
    except (IndexError, ValueError):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    is_fluid = bool(reminder and getattr(reminder, "is_fluid_habit", False))
    if reminder and (_is_habit_like(reminder) or is_fluid):
        record_kwargs: dict[str, Any] = {}
        if is_fluid:
            try:
                tz = pytz.timezone(user.timezone)
            except Exception:
                tz = pytz.UTC
            record_kwargs["local_date"] = datetime.now(tz).date().isoformat()
        else:
            record_kwargs["due_at_utc_naive"] = reminder.habit_active_due_at
        recorded = await habit_event_dao.record(
            reminder=reminder,
            user_tz=user.timezone,
            outcome="not_today",
            source="wrapup",
            **record_kwargs,
        )
        if recorded:
            if is_fluid:
                reminder.fluid_streak_current = 0
            else:
                reminder.habit_streak_current = 0
            await reminder_dao.mark_habit_not_today(reminder.id)

    status_text = l10n.get("btn_not_done_short", "Not done")
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_replace_wrapup_row(
                callback.message.reply_markup,
                callback_data=callback.data,
                status_text=status_text,
            )
        )
    except TelegramBadRequest:
        pass
    await callback.answer(l10n.get("wrapup_not_done_saved", "❌ Left as not done for tonight."))

@router.callback_query(F.data.startswith("done_task_"))
async def callback_task_done(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    payload = callback.data[len("done_task_"):]
    parts = payload.split("_")
    try:
        reminder_id = int(parts[0])
    except (IndexError, ValueError):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    cycle_due_at_utc_naive = None
    if len(parts) >= 2:
        try:
            cycle_due_ts = int(parts[1])
            cycle_due_at_utc_naive = datetime.fromtimestamp(cycle_due_ts, tz=timezone.utc).replace(tzinfo=None)
        except ValueError:
            cycle_due_at_utc_naive = None

    reminder = await reminder_dao.get_owned(reminder_id, user.id)

    # Idempotency: ignore rapid double-taps
    if (
        not reminder
        or reminder.status == "completed"
        or (
            reminder.is_recurring
            and reminder.completed_for_execution_time is not None
            and reminder.completed_for_execution_time >= reminder.execution_time
        )
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
            tz = pytz.timezone(user.timezone)
        except Exception:
            tz = pytz.UTC
        await habit_event_dao.record(
            reminder=reminder,
            user_tz=user.timezone,
            outcome="done",
            source="button",
            local_date=datetime.now(tz).date().isoformat(),
        )
        try:
            done_text = f"{escape_markdown_v2(callback.message.text)}\n\n{_pick_done_reply(l10n)}"
            await callback.message.edit_text(done_text, reply_markup=None, parse_mode="MarkdownV2")
        except TelegramBadRequest:
            pass
        await callback.answer(l10n["btn_done"])
        return

    credited_due_at_utc_naive = None
    if _is_habit_like(reminder):
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
            await habit_event_dao.record(
                reminder=reminder,
                user_tz=user.timezone,
                outcome="done",
                source="button",
                due_at_utc_naive=due_at,
            )
            credited_due_at_utc_naive = due_at

    await reminder_dao.mark_done(reminder_id)
    if not reminder.is_recurring:
        scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)

    try:
        done_text = f"{escape_markdown_v2(callback.message.text)}\n\n{_pick_done_reply(l10n)}"
        await callback.message.edit_text(
            done_text,
            reply_markup=get_done_followup_keyboard(
                reminder_id=reminder.id,
                l10n=l10n,
                is_recurring=bool(reminder.is_recurring),
                cycle_due_ts=(
                    int(credited_due_at_utc_naive.replace(tzinfo=timezone.utc).timestamp())
                    if credited_due_at_utc_naive is not None
                    else None
                ),
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


@router.callback_query(F.data.startswith("done_note_"))
async def callback_done_note(
    callback: CallbackQuery,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder_id = int(callback.data.split("done_note_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
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
    user: User,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    reminder_id = data.get("done_note_reminder_id")
    if not reminder_id:
        await state.clear()
        return
    if not await reminder_dao.get_owned(int(reminder_id), user.id):
        await state.clear()
        await message.answer(l10n["item_not_found"])
        return
    note = (message.text or "").strip()
    if not note:
        await message.answer(l10n.get("done_note_prompt", "Send a short completion note."))
        return
    await reminder_dao.set_last_completion_note(int(reminder_id), note[:400])
    await state.clear()
    await message.answer(l10n.get("done_note_saved", "✅ Note saved."))


@router.callback_query(F.data.startswith("done_skip_next_"))
async def callback_done_skip_next(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    reminder_id = int(callback.data.split("done_skip_next_")[1])
    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder or not reminder.is_recurring or not reminder.rrule_string:
        return await callback.answer(l10n.get("done_skip_next_failed", "❌ I couldn't skip next occurrence for this task."), show_alert=True)

    try:
        next_run_utc_naive = next_occurrence_utc(
            reminder.rrule_string, reminder.execution_time, user.timezone, reminder.execution_time
        )
        if not next_run_utc_naive:
            return await callback.answer(l10n.get("done_skip_next_failed", "❌ I couldn't skip next occurrence for this task."), show_alert=True)
        reminder.execution_time = next_run_utc_naive
        reminder.completed_for_execution_time = None
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None
        scheduler_service.schedule_reminder(
            reminder.id,
            to_utc_aware(next_run_utc_naive),
            is_nagging=reminder.is_nagging,
        )
        scheduler_service.remove_nagging_job(reminder.id)
    except Exception:
        await reminder_dao.session.rollback()
        return await callback.answer(l10n.get("done_skip_next_failed", "❌ I couldn't skip next occurrence for this task."), show_alert=True)

    next_str = format_time(next_run_utc_naive, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
    await callback.message.answer(
        l10n.get("done_skip_next_done", "⏭ Next occurrence skipped. New time: {time}").format(time=next_str)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("done_undo_"))
async def callback_done_undo(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    payload = callback.data[len("done_undo_"):]
    parts = payload.split("_")
    try:
        reminder_id = int(parts[0])
    except (IndexError, ValueError):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    # 3.4: the cycle Done actually credited, embedded by
    # get_done_followup_keyboard — same pattern done_task_/not_today_ already
    # use. Falls back to habit_active_due_at for a followup keyboard sent
    # before this fix (no suffix), same as before.
    cycle_due_at_utc_naive = None
    if len(parts) >= 2:
        try:
            cycle_due_ts = int(parts[1])
            cycle_due_at_utc_naive = datetime.fromtimestamp(cycle_due_ts, tz=timezone.utc).replace(tzinfo=None)
        except ValueError:
            cycle_due_at_utc_naive = None

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    # Undo must remove the recorded "done" event, or habit reports will keep
    # showing a completion the user just took back.
    if _is_habit_like(reminder):
        due_at = cycle_due_at_utc_naive or reminder.habit_active_due_at or reminder.execution_time
        if due_at is not None:
            await habit_event_dao.delete_for_cycle(reminder.id, cycle_key_for_fixed(due_at))
            await reminder_dao.revert_habit_streak_completion(reminder.id, due_at_utc_naive=due_at)

    reminder.status = "pending"
    reminder.completed_at = None
    reminder.completed_for_execution_time = None
    reminder.last_completion_note = None
    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None

    now_utc = datetime.now(pytz.UTC).replace(tzinfo=None)
    execution_time_cmp = reminder.execution_time
    if execution_time_cmp.tzinfo is not None:
        execution_time_cmp = execution_time_cmp.astimezone(pytz.UTC).replace(tzinfo=None)
    if execution_time_cmp <= now_utc:
        reminder.execution_time = now_utc + timedelta(minutes=15)
    try:
        scheduler_service.schedule_reminder(reminder.id, reminder.execution_time, is_nagging=reminder.is_nagging)
    except Exception:
        await reminder_dao.session.rollback()
        return await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again."), show_alert=True)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(l10n.get("done_undo_done", "↩ Done action was undone. Task is active again."))
    await callback.answer()


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("snooze_show_"))
async def callback_snooze_show(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    reminder_id = int(callback.data.split("snooze_show_")[1])
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
        new_time = now.replace(hour=hour_map[action], minute=0, second=0, microsecond=0)
        if new_time <= now:
            new_time += timedelta(days=1)
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

    friendly_time = format_time(new_time_utc_naive, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
    snoozed_line = l10n["snoozed_until"].format(time=escape_markdown_v2(friendly_time))
    snooze_text = f"{escape_markdown_v2(callback.message.text)}\n\n{snoozed_line}"
    await callback.message.edit_text(
        snooze_text,
        reply_markup=None,
        parse_mode="MarkdownV2",
    )
    await callback.answer(l10n["snoozed_toast"])


# ---------------------------------------------------------------------------
# Catch-all: non-text messages outside any FSM flow
# ---------------------------------------------------------------------------
# REWORK_PLAN_3 2.7: nothing responded to a photo/voice/video/sticker/etc.
# sent with StateFilter(None) — the bot looked broken, silently swallowing
# the update. Registered last so every more specific handler (forwarded
# messages, FSM-state text handlers, callbacks) gets first refusal; ~F.text
# means an ordinary text message never reaches this either way, since
# handle_task_text above already claims those.

@router.message(StateFilter(None), ~F.text)
async def handle_non_text_message(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(l10n.get("text_only_hint", "📝 I can only understand text right now. Send your reminder as a text message."))
