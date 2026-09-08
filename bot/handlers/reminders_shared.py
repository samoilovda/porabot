"""Shared state, constants, and helpers for the bot.handlers.reminders_*
family of modules (4.1: split out of the original 2300-line
bot/handlers/reminders.py — see that file's module docstring for why).

Nothing in this module registers a handler or owns a Router; it exists so
reminders_wizard/repeat/listing/completion/snooze.py — and bot/handlers/
habits.py, bot/handlers/menu.py, bot/__main__.py, which import a few of
these directly — share one copy of this state instead of each
reimplementing it.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.database.models import is_habit_like as _is_habit_like
from bot.keyboards.inline import (
    TASKS_PAGE_SIZE,
    get_edit_keyboard,
    get_parse_confirmation_keyboard,
    get_repeat_builder_keyboard,
    get_time_selection_keyboard,
)
from bot.lexicon import ALL_MENU_BUTTON_TEXTS
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown, escape_markdown_v2
from bot.utils.tags import extract_tags_and_priority, format_tags, priority_glyph
from bot.utils.time_ext import format_time, next_occurrence_utc, to_utc_aware, to_utc_naive

logger = logging.getLogger(__name__)

# asyncio.Task registry for auto-removing inline keyboards after 5 s
active_auto_delete_tasks: dict[tuple[int, int], asyncio.Task] = {}

_UNDO_DELETE_WINDOW = 5

_MENU_TEXTS = ALL_MENU_BUTTON_TEXTS
_MAX_INPUT = 3000
_NAG_LIMIT_MIN = 0
_NAG_LIMIT_MAX = 20
_COMPLETED_HISTORY_DAYS = 7
# 1.5: caps the "recently completed" screen's item count — see limit_items'
# module docstring (bot/utils/pagination.py) for why this is needed at all.
_COMPLETED_HISTORY_LIMIT = 20
_PARSE_CONFIDENCE_THRESHOLD = 0.7
# Telegram caps inline keyboards well below 100 buttons and messages at 4096
# chars. get_tasks_list_keyboard adds up to 3 buttons per task, so an
# unbounded task list can blow both limits and the list silently fails to
# render at all. Page what's shown/rendered into buttons instead of just
# truncating with an unreachable "...and N more" tail.
# The value itself lives in bot.keyboards.inline as TASKS_PAGE_SIZE (single
# source of truth shared with get_tasks_list_keyboard) — this name is kept
# as a local alias so callers across this module family don't need touching.
_TASKS_PAGE_SIZE = TASKS_PAGE_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RRULE_WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_RRULE_WEEKDAYS_SET = {"MO", "TU", "WE", "TH", "FR"}
_RRULE_WEEKEND_SET = {"SA", "SU"}


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

