"""Marking a reminder done: the evening wrap-up's inline row swap, the
main done_task_/done_undo_/done_note_/done_skip_next_ family, and the
done-followup keyboard actions (4.1 — split out of the original
bot/handlers/reminders.py; see bot/handlers/reminders.py's module
docstring for the full module family and why).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.dao.habit_event import HabitEventDAO, cycle_key_for_fixed
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.database.models import is_habit_like as _is_habit_like
from bot.handlers.reminders_shared import _pick_done_reply
from bot.keyboards.inline import get_done_followup_keyboard
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown_v2
from bot.utils.time_ext import format_time, next_occurrence_utc, to_utc_aware

router = Router(name="reminders_completion")
logger = logging.getLogger(__name__)



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


