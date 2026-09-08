"""Task list rendering/pagination, /find and its filters (today/week/
overdue/recurring/tags), the missed-recovery digest's bulk actions, the
per-reminder nag-limit prompt, and the recently-completed-tasks screen
(4.1 — split out of the original bot/handlers/reminders.py; see
bot/handlers/reminders.py's module docstring for the full module family
and why).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import bot.handlers.reminders_shared as reminders_shared
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.database.models import is_habit_like as _is_habit_like
from bot.handlers.reminders_shared import (
    _COMPLETED_HISTORY_DAYS,
    _COMPLETED_HISTORY_LIMIT,
    _NAG_LIMIT_MAX,
    _NAG_LIMIT_MIN,
    _TASKS_PAGE_SIZE,
    _format_task_line_md2,
    _message_task_key,
    _paginate_tasks_for_list,
    _remove_keyboard_after_delay,
    _render_tasks_list_text,
    _reschedule_current_execution,
    _reset_auto_delete,
    _rrule_text,
    active_auto_delete_tasks,
)
from bot.keyboards.inline import (
    get_completed_tasks_keyboard,
    get_edit_keyboard,
    get_filtered_tasks_keyboard,
    get_tags_menu_keyboard,
    get_tasks_list_keyboard,
    get_undo_delete_keyboard,
)
from bot.services.missed_recovery import RECOVERY_DIGEST_LIMIT
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown, escape_markdown_v2
from bot.utils.pagination import limit_items, preview_line
from bot.utils.time_ext import format_time, next_occurrence_utc, to_utc_aware

router = Router(name="reminders_listing")
logger = logging.getLogger(__name__)



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
        seconds=reminders_shared._UNDO_DELETE_WINDOW
    )
    await reminder_dao.session.commit()
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(
        l10n["task_deleted"], reply_markup=get_undo_delete_keyboard(task_id, l10n)
    )
    task = asyncio.create_task(_remove_keyboard_after_delay(callback.message, reminders_shared._UNDO_DELETE_WINDOW))
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
    if len(tasks) > _TASKS_PAGE_SIZE:
        # fix(2.3): the list is silently cut to _TASKS_PAGE_SIZE (Telegram
        # message/keyboard limits) — without this, a user sees 25 lines and
        # has no way to know there were more matches.
        lines.append(
            l10n.get("find_truncated_notice", "Showing first {shown} of {total}.").format(
                shown=_TASKS_PAGE_SIZE, total=len(tasks)
            )
        )
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

    # 1.5: bound both item COUNT and per-item text length — an unbounded
    # history (recurring tasks completed daily over _COMPLETED_HISTORY_DAYS)
    # could otherwise exceed Telegram's 4096-char message limit and make
    # edit_text raise, dropping the whole screen. Same shape daily_briefs
    # already uses for the morning/evening brief.
    shown_completed, hidden_completed = limit_items(completed, _COMPLETED_HISTORY_LIMIT)
    lines = [l10n["completed_header"]]
    for task in shown_completed:
        completed_dt = task.completed_at or task.execution_time
        dt_str = escape_markdown_v2(format_time(completed_dt, user.timezone, user.show_utc_offset, "%d.%m %H:%M"))
        note = preview_line(task.last_completion_note) if task.last_completion_note else None
        note_suffix = f" — {escape_markdown_v2(note)}" if note else ""
        lines.append(f"✅ `{dt_str}`: ~{escape_markdown_v2(preview_line(task.reminder_text))}~{note_suffix}")
    if hidden_completed:
        lines.append(l10n.get("brief_items_more", "…and {count} more").format(count=hidden_completed))

    safe_text = "\n".join(lines)
    try:
        await callback.message.edit_text(
            safe_text, reply_markup=get_completed_tasks_keyboard(l10n), parse_mode="MarkdownV2"
        )
    except TelegramBadRequest:
        pass  # Concurrent tap or an edge case the length bound above didn't fully catch — safe to ignore.
    await callback.answer()


