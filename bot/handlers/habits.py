"""Habits handler — true daily custom habits builder."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.habit_event import HabitEventDAO
from bot.keyboards.inline import get_fluid_pick_time_keyboard
from bot.services.scheduler import SchedulerService
from bot.services.parser import InputParser
from bot.utils.markdown import escape_markdown
from bot.utils.time_ext import format_time, next_occurrence_utc, to_utc_aware, to_utc_naive

router = Router(name="habits")
logger = logging.getLogger(__name__)

class HabitState(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()
    waiting_for_fluid_name = State()
    waiting_for_fluid_time = State()

def _preset_habit_texts(l10n: dict[str, Any]) -> dict[str, str]:
    return {
        "workout": l10n["habit_preset_workout"],
        "water": l10n["habit_preset_water"],
        "rest": l10n["habit_preset_rest"],
    }

def _habit_motivation_text(l10n: dict[str, Any], stats: dict[str, int]) -> str:
    return l10n.get("habit_motivation", "").format(
        weekly_done=stats.get("weekly_done", 0),
        active_count=stats.get("active_count", 0),
        best_current_streak=stats.get("best_current_streak", 0),
        best_ever_streak=stats.get("best_ever_streak", 0),
    )

def _is_habit_entry(reminder) -> bool:
    return bool(
        getattr(reminder, "is_habit", False)
        or getattr(reminder, "is_fluid_habit", False)
        or (
            bool(getattr(reminder, "is_recurring", False))
            and bool(getattr(reminder, "is_nagging", False))
            and str(getattr(reminder, "rrule_string", "") or "").upper().startswith("FREQ=DAILY")
        )
        or getattr(reminder, "habit_active_due_at", None) is not None
        or getattr(reminder, "habit_last_completed_due_at", None) is not None
        or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
        or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
    )

def _is_habit_like(reminder) -> bool:
    """Fixed-cycle habit detection (mirrors SchedulerService._is_habit_like)."""
    if getattr(reminder, "is_fluid_habit", False):
        return False
    return bool(
        getattr(reminder, "is_habit", False)
        or getattr(reminder, "habit_active_due_at", None) is not None
        or getattr(reminder, "habit_last_completed_due_at", None) is not None
        or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
        or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
    )


def _habit_streak_labels(reminder) -> tuple[int, int]:
    if getattr(reminder, "is_fluid_habit", False):
        return (
            max(0, int(getattr(reminder, "fluid_streak_current", 0) or 0)),
            max(0, int(getattr(reminder, "fluid_streak_best", 0) or 0)),
        )
    return (
        max(0, int(getattr(reminder, "habit_streak_current", 0) or 0)),
        max(0, int(getattr(reminder, "habit_streak_best", 0) or 0)),
    )


def _fluid_mode_label(reminder, l10n: dict[str, Any]) -> str:
    if not getattr(reminder, "is_fluid_habit", False):
        return l10n.get("habit_mode_fixed", "fixed")
    mode = str(getattr(reminder, "fluid_mode", "") or "brief_only")
    if mode == "ask_time":
        return l10n.get("habit_mode_fluid_ask_time", "fluid: ask time daily")
    return l10n.get("habit_mode_fluid_brief_only", "fluid: morning+evening checks")


async def _schedule_fluid_habit_for_today(
    *,
    reminder,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    hhmm: str,
) -> None:
    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC

    now_local = datetime.now(user_tz)
    hour, minute = hhmm.split(":", 1)
    target_local = now_local.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if target_local <= now_local:
        raise ValueError("past_time")

    target_utc_naive = to_utc_naive(target_local)
    reminder.execution_time = target_utc_naive
    reminder.completed_for_execution_time = None
    reminder.fluid_planned_date = now_local.date().isoformat()
    reminder.fluid_planned_time = hhmm
    reminder.last_nag_chat_id = None
    reminder.last_nag_message_id = None
    scheduler_service.schedule_reminder(reminder.id, to_utc_aware(target_utc_naive), is_nagging=False)
    scheduler_service.remove_nagging_job(reminder.id)
    await reminder_dao.session.flush()


def get_habits_keyboard(l10n: dict[str, Any]) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["habit_preset_water"], callback_data="habit_preset_water"),
        InlineKeyboardButton(text=l10n["habit_preset_workout"], callback_data="habit_preset_workout")
    )
    builder.row(
        InlineKeyboardButton(text=l10n["habit_preset_rest"], callback_data="habit_preset_rest"),
        InlineKeyboardButton(text=l10n["habit_btn_custom"], callback_data="habit_custom")
    )
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_fluid"], callback_data="habit_fluid"))
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_list"], callback_data="habit_list"))
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_cancel"], callback_data="habit_cancel"))
    return builder

# NOTE: the "🫧 Habits" main-menu button handler lives in bot/handlers/menu.py
# (registered on an earlier router) so it can't be swallowed by another
# router's stateful FSM handlers. _habit_motivation_text and
# get_habits_keyboard above are still used throughout this module and
# imported from here by menu.py's btn_habits.

@router.callback_query(F.data == "habit_cancel")
async def cb_habit_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer(l10n["habit_cancelled"])

@router.callback_query(F.data.startswith("habit_preset_"))
async def cb_habit_preset(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    preset_key = callback.data.split("_")[-1]
    habit_text = _preset_habit_texts(l10n).get(preset_key, l10n["habit_unknown"])
    
    await state.update_data(habit_text=habit_text)
    await state.set_state(HabitState.waiting_for_time)
    
    await callback.message.edit_text(
        l10n["habit_selected_prompt"].format(habit=habit_text),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "habit_custom")
async def cb_habit_custom(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(HabitState.waiting_for_name)
    await callback.message.edit_text(
        l10n["habit_custom_prompt"],
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "habit_fluid")
async def cb_habit_fluid(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(HabitState.waiting_for_fluid_name)
    await callback.message.edit_text(
        l10n["habit_fluid_name_prompt"],
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(HabitState.waiting_for_fluid_name)
async def state_fluid_habit_name(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    if not message.text:
        return
    habit_text = message.text.strip()
    await state.update_data(habit_text=habit_text)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n["habit_fluid_mode_brief_only"],
            callback_data="habit_fluid_mode_brief_only",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=l10n["habit_fluid_mode_ask_time"],
            callback_data="habit_fluid_mode_ask_time",
        )
    )
    await message.answer(
        l10n["habit_fluid_mode_prompt"],
        reply_markup=builder.as_markup(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("habit_fluid_mode_"))
async def cb_fluid_habit_mode(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    reminder_dao: ReminderDAO,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    habit_text = data.get("habit_text", l10n["habit_default_name"])
    mode = callback.data.removeprefix("habit_fluid_mode_")
    if mode not in {"brief_only", "ask_time"}:
        await callback.answer(l10n["invalid_mode"], show_alert=True)
        return

    try:
        now_utc = datetime.now(pytz.UTC).replace(tzinfo=None)
        reminder = await reminder_dao.create_reminder(
            user_id=user.id,
            text=habit_text,
            execution_time=now_utc + timedelta(days=365 * 5),
            is_recurring=True,
            rrule_string=None,
            is_habit=True,
            is_fluid_habit=True,
            fluid_mode=mode,
            is_nagging=False,
            nagging_max_repeats=0,
        )
        await reminder_dao.session.flush()
    except ValueError as ve:
        logger.error("Validation error: %s", ve)
        await callback.message.answer(l10n["habit_create_failed_long"])
        await callback.answer()
        return
    except Exception as e:
        logger.error("Error creating fluid habit: %s", e, exc_info=True)
        await reminder_dao.session.rollback()
        await callback.message.answer(l10n["habit_create_failed_internal"])
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        l10n["habit_fluid_created"].format(
            habit=escape_markdown(habit_text),
            mode=l10n["habit_fluid_mode_brief_only"] if mode == "brief_only" else l10n["habit_fluid_mode_ask_time"],
        ),
        parse_mode="Markdown",
    )
    await callback.answer()

@router.message(HabitState.waiting_for_name)
async def state_habit_name(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    if not message.text:
        return
    await state.update_data(habit_text=message.text.strip())
    await state.set_state(HabitState.waiting_for_time)
    await message.answer(l10n["habit_time_prompt"])

@router.message(HabitState.waiting_for_time)
async def state_habit_time(
    message: Message, 
    state: FSMContext, 
    user: User, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    if not message.text:
        return
        
    data = await state.get_data()
    habit_text = data.get("habit_text", l10n["habit_default_name"])
    
    parser = InputParser()
    result = await parser.parse(message.text, user.timezone)
    
    if not result.parsed_datetime:
        await message.answer(l10n["habit_time_retry"])
        return
        
    # Normalize to UTC once and store as naive UTC in DB.
    execution_time_utc = to_utc_naive(result.parsed_datetime)

    # The parser can return an ambiguous/past time (e.g. "at 9" when it's
    # already past 9 today in the user's zone). Since this habit is always
    # FREQ=DAILY, just advance to the next daily slot instead of creating a
    # reminder whose date-job would fire the instant it's scheduled.
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if execution_time_utc <= now_utc_naive:
        advanced = next_occurrence_utc("FREQ=DAILY", execution_time_utc, user.timezone, now_utc_naive)
        execution_time_utc = advanced or (execution_time_utc + timedelta(days=1))

    # Schedule it as a strict daily recurring task with nagging enabled
    try:
        reminder = await reminder_dao.create_reminder(
            user_id=user.id,
            text=habit_text,
            execution_time=execution_time_utc,
            is_recurring=True,
            rrule_string="FREQ=DAILY",
            is_habit=True,
            is_nagging=True,
            nagging_max_repeats=3,
        )
        scheduler_service.schedule_reminder(
            reminder.id,
            to_utc_aware(execution_time_utc),
            is_nagging=reminder.is_nagging,
        )
        
        time_str = format_time(execution_time_utc, user.timezone, user.show_utc_offset, "%H:%M")
        await message.answer(
            l10n["habit_created"].format(habit=escape_markdown(habit_text), time=time_str),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError as ve:
        logger.error("Validation error: %s", ve)
        await message.answer(l10n["habit_create_failed_long"])
    except Exception as e:
        logger.error("Error creating habit: %s", e, exc_info=True)
        # Keep DB and scheduler state consistent if scheduling fails mid-flow.
        await reminder_dao.session.rollback()
        await message.answer(l10n["habit_create_failed_internal"])
        
@router.callback_query(F.data == "habit_list")
async def cb_habit_list(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO, l10n: dict[str, Any]
) -> None:
    # get_user_reminders excludes fluid habits (they live in their own UI
    # flow), so without merging in get_active_fluid_habits here, fluid
    # habits would never show up in "My Habits" — making them impossible to
    # delete short of the account-wide "Clear all" reset.
    reminders = await reminder_dao.get_user_reminders(user.id)
    fluid_reminders = await reminder_dao.get_active_fluid_habits(user.id)
    # Filter for active recurring tasks
    habits = [r for r in reminders if r.status == "pending" and _is_habit_entry(r)] + [
        r for r in fluid_reminders if _is_habit_entry(r)
    ]

    if not habits:
        await callback.message.edit_text(
            l10n["habit_no_active"],
            reply_markup=get_habits_keyboard(l10n).as_markup(),
            parse_mode="Markdown",
        )
        await callback.answer()
        return
        
    # Build an inline keyboard with delete buttons for each habit
    builder = InlineKeyboardBuilder()
    text_lines = [l10n["habit_list_header"]]

    try:
        today_str = datetime.now(pytz.timezone(user.timezone)).date().isoformat()
    except Exception:
        today_str = datetime.now(pytz.UTC).date().isoformat()

    for i, h in enumerate(habits, start=1):
        streak, best = _habit_streak_labels(h)
        if h.is_fluid_habit:
            # fluid_planned_time is only meaningful for today's cycle — a stale
            # value from a previous day must not be displayed as if still valid.
            planned_time = getattr(h, "fluid_planned_time", None)
            if getattr(h, "fluid_planned_date", None) == today_str and planned_time:
                time_str = planned_time
            else:
                time_str = l10n.get("habit_fluid_time_anytime", "anytime")
        else:
            time_str = format_time(h.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        text_lines.append(
            l10n["habit_list_item"].format(
                index=i,
                habit=escape_markdown(h.reminder_text),
                time=time_str,
                streak=streak,
                best=best,
                mode=_fluid_mode_label(h, l10n),
            )
        )
        # Settings + deletion buttons. Fluid habits are scheduled entirely
        # through the fluid-specific flow (_schedule_fluid_habit_for_today),
        # not the repeat/nagging toggles that task_settings exposes — those
        # would set fields the fluid path never reads, so skip the gear for
        # them and only offer delete.
        row = []
        if not h.is_fluid_habit:
            row.append(
                InlineKeyboardButton(
                    text=l10n.get("btn_task_settings", "⚙️"),
                    callback_data=f"task_settings_{h.id}",
                )
            )
        row.append(
            InlineKeyboardButton(
                text=l10n["habit_btn_delete_n"].format(index=i),
                callback_data=f"del_habit_{h.id}",
            )
        )
        builder.row(*row)

    builder.row(InlineKeyboardButton(text=l10n["habit_btn_back_dashboard"], callback_data="habit_back_dash"))
    
    await callback.message.edit_text(
        "\n".join(text_lines), 
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "habit_back_dash")
async def cb_habit_back_dash(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    user: User,
) -> None:
    await state.clear()
    stats = await reminder_dao.get_habit_motivation_stats(user.id, user.timezone, days=7)
    motivation = _habit_motivation_text(l10n, stats)
    text = l10n["habits_dashboard"]
    if motivation.strip():
        text = f"{text}\n\n{motivation}"
    await callback.message.edit_text(
        text,
        reply_markup=get_habits_keyboard(l10n).as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fluid_done_"))
async def cb_fluid_done(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    try:
        reminder_id = int(callback.data.split("fluid_done_")[1])
    except Exception:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    if not await reminder_dao.get_owned(reminder_id, user.id):
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return

    done = await reminder_dao.mark_fluid_habit_done_today(reminder_id, user.timezone)
    if done:
        reminder = await reminder_dao.get_by_id(reminder_id)
        if reminder:
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
        await callback.answer(l10n.get("fluid_done_saved", "✅ Marked as done for today."))
    else:
        await callback.answer(l10n.get("already_done", "Already done ✅"))

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("fluid_pick_custom_"))
async def cb_fluid_pick_custom(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    try:
        reminder_id = int(callback.data.split("fluid_pick_custom_")[1])
    except Exception:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return
    await state.update_data(fluid_pick_reminder_id=reminder_id)
    await state.set_state(HabitState.waiting_for_fluid_time)
    await callback.message.answer(l10n["fluid_time_custom_prompt"], parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("fluid_pick_"))
async def cb_fluid_pick_time(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    try:
        _, _, reminder_id_raw, hhmm_raw = callback.data.split("_", 3)
        reminder_id = int(reminder_id_raw)
        hhmm = f"{hhmm_raw[:2]}:{hhmm_raw[2:]}"
    except Exception:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    if not reminder or not reminder.is_fluid_habit:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return

    try:
        await _schedule_fluid_habit_for_today(
            reminder=reminder,
            user=user,
            reminder_dao=reminder_dao,
            scheduler_service=scheduler_service,
            hhmm=hhmm,
        )
    except ValueError:
        await callback.answer(l10n.get("fluid_time_past", "❌ This time has already passed today."), show_alert=True)
        return
    except Exception as e:
        logger.error("Failed to schedule fluid habit %s: %s", reminder_id, e, exc_info=True)
        await reminder_dao.session.rollback()
        await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again."), show_alert=True)
        return

    await callback.answer(l10n["fluid_time_saved"].format(time=hhmm))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.message(HabitState.waiting_for_fluid_time)
async def state_fluid_pick_manual_time(
    message: Message,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    raw = (message.text or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer(l10n["fluid_time_invalid"], parse_mode="Markdown")
        return
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        await message.answer(l10n["fluid_time_invalid"], parse_mode="Markdown")
        return

    data = await state.get_data()
    reminder_id = data.get("fluid_pick_reminder_id")
    if not reminder_id:
        await state.clear()
        return

    reminder = await reminder_dao.get_owned(int(reminder_id), user.id)
    if not reminder or not reminder.is_fluid_habit:
        await state.clear()
        await message.answer(l10n["item_not_found"])
        return

    hhmm = f"{hh:02d}:{mm:02d}"
    try:
        await _schedule_fluid_habit_for_today(
            reminder=reminder,
            user=user,
            reminder_dao=reminder_dao,
            scheduler_service=scheduler_service,
            hhmm=hhmm,
        )
    except ValueError:
        await message.answer(l10n.get("fluid_time_past", "❌ This time has already passed today."), parse_mode="Markdown")
        return
    except Exception as e:
        logger.error("Failed manual fluid schedule %s: %s", reminder.id, e, exc_info=True)
        await reminder_dao.session.rollback()
        await message.answer(l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again."))
        return

    await state.clear()
    await message.answer(l10n["fluid_time_saved"].format(time=hhmm), parse_mode="Markdown")

@router.callback_query(F.data.startswith("not_today_"))
async def cb_not_today(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    payload = callback.data[len("not_today_"):]
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

    reminder = await reminder_dao.get_by_id(reminder_id)
    is_fluid = bool(reminder and getattr(reminder, "is_fluid_habit", False))
    if not reminder or not (_is_habit_like(reminder) or is_fluid):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    record_kwargs: dict[str, Any] = {}
    if is_fluid:
        try:
            tz = pytz.timezone(user.timezone)
        except Exception:
            tz = pytz.UTC
        record_kwargs["local_date"] = datetime.now(tz).date().isoformat()
    else:
        record_kwargs["due_at_utc_naive"] = cycle_due_at_utc_naive or reminder.habit_active_due_at

    recorded = await habit_event_dao.record(
        reminder=reminder,
        user_tz=user.timezone,
        outcome="not_today",
        source="button",
        **record_kwargs,
    )
    if not recorded:
        await callback.answer(l10n.get("already_done", "Already done ✅"))
        return

    if is_fluid:
        reminder.fluid_streak_current = 0
    else:
        reminder.habit_streak_current = 0
    await reminder_dao.mark_habit_not_today(reminder.id)
    scheduler_service.remove_nagging_job(reminder.id)

    try:
        saved_text = f"{escape_markdown(callback.message.text)}\n\n{l10n['habit_not_today_saved']}"
        await callback.message.edit_text(saved_text, reply_markup=None, parse_mode="Markdown")
    except TelegramBadRequest:
        pass  # Concurrent tap — safe to ignore

    await callback.answer(l10n["habit_not_today_saved"])


@router.callback_query(F.data.startswith("del_habit_"))
async def cb_del_habit(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    task_id = int(callback.data.split("_")[-1])
    if not await reminder_dao.get_owned(task_id, user.id):
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return
    try:
        await habit_event_dao.delete_for_reminder(task_id)
        await reminder_dao.delete_by_id(task_id)
        scheduler_service.remove_reminder_job(task_id)
            
        await callback.answer(l10n["habit_deleted_alert"], show_alert=True)
        await callback.message.delete()
        await callback.message.answer(
            l10n["habit_deleted_followup"],
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Error deleting habit %s: %s", task_id, e)
        await callback.answer(l10n["habit_delete_error_alert"], show_alert=True)
