"""RRULE repeat builder (rrb_* callbacks), plus the nagging-toggle and
delete actions on an existing reminder's edit keyboard (4.1 — split out of
the original bot/handlers/reminders.py; see bot/handlers/reminders.py's
module docstring for the full module family and why).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import bot.handlers.reminders_shared as reminders_shared
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.reminders_shared import (
    _RRULE_WEEKDAY_CODES,
    _apply_repeat_change,
    _message_task_key,
    _parse_rrule_parts,
    _remove_keyboard_after_delay,
    _render_repeat_builder,
    _reschedule_current_execution,
    _reset_auto_delete,
    _rrule_end_label,
    _rrule_text,
    active_auto_delete_tasks,
)
from bot.keyboards.inline import (
    get_edit_keyboard,
    get_repeat_builder_keyboard,
    get_repeat_end_keyboard,
    get_repeat_weekday_keyboard,
    get_undo_delete_keyboard,
)
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.markdown import escape_markdown

router = Router(name="reminders_repeat")
logger = logging.getLogger(__name__)


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
        seconds=reminders_shared._UNDO_DELETE_WINDOW
    )
    await reminder_dao.session.commit()
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(
        l10n["task_deleted"], reply_markup=get_undo_delete_keyboard(reminder_id, l10n)
    )
    task = asyncio.create_task(_remove_keyboard_after_delay(callback.message, reminders_shared._UNDO_DELETE_WINDOW))
    active_auto_delete_tasks[_message_task_key(callback.message)] = task


