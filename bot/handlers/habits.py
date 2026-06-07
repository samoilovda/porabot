"""Habits handler — daily habit builder (fixed-time and fluid)."""

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.callbacks import (
    DelHabitCallback,
    FluidDoneCallback,
    FluidPickCustomCallback,
    FluidPickTimeCallback,
    HabitFluidModeCallback,
    HabitPresetCallback,
    TaskSettingsCallback,
)
from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO
from bot.keyboards.inline import get_fluid_pick_time_keyboard
from bot.services.scheduler import SchedulerService
from bot.services.parser import InputParser
from bot.utils.habits_utils import is_habit_entry
from bot.utils.time_ext import format_time, resolve_tz, to_utc_aware, to_utc_naive

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
        "water":   l10n["habit_preset_water"],
        "rest":    l10n["habit_preset_rest"],
    }


def _habit_motivation_text(l10n: dict[str, Any], stats: dict[str, int]) -> str:
    return l10n.get("habit_motivation", "").format(
        weekly_done=stats.get("weekly_done", 0),
        active_count=stats.get("active_count", 0),
        best_current_streak=stats.get("best_current_streak", 0),
        best_ever_streak=stats.get("best_ever_streak", 0),
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
    user_tz = resolve_tz(user.timezone)
    now_local = datetime.now(user_tz)
    hour_str, minute_str = hhmm.split(":", 1)
    target_local = now_local.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
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
        InlineKeyboardButton(text=l10n["habit_preset_water"],   callback_data=HabitPresetCallback(key="water").pack()),
        InlineKeyboardButton(text=l10n["habit_preset_workout"], callback_data=HabitPresetCallback(key="workout").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["habit_preset_rest"],    callback_data=HabitPresetCallback(key="rest").pack()),
        InlineKeyboardButton(text=l10n["habit_btn_custom"],     callback_data="habit_custom"),
    )
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_fluid"],  callback_data="habit_fluid"))
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_list"],   callback_data="habit_list"))
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_cancel"], callback_data="habit_cancel"))
    return builder


@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits", "🫧 Hábitos"]))
async def btn_habits(
    message: Message,
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
    await message.answer(text, reply_markup=get_habits_keyboard(l10n).as_markup(), parse_mode="Markdown")


@router.callback_query(F.data == "habit_cancel")
async def cb_habit_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer(l10n["habit_cancelled"])


@router.callback_query(HabitPresetCallback.filter())
async def cb_habit_preset(
    callback: CallbackQuery,
    callback_data: HabitPresetCallback,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    habit_text = _preset_habit_texts(l10n).get(callback_data.key, l10n["habit_unknown"])
    await state.update_data(habit_text=habit_text)
    await state.set_state(HabitState.waiting_for_time)
    await callback.message.edit_text(
        l10n["habit_selected_prompt"].format(habit=habit_text),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "habit_custom")
async def cb_habit_custom(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(HabitState.waiting_for_name)
    await callback.message.edit_text(l10n["habit_custom_prompt"], parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "habit_fluid")
async def cb_habit_fluid(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(HabitState.waiting_for_fluid_name)
    await callback.message.edit_text(l10n["habit_fluid_name_prompt"], parse_mode="Markdown")
    await callback.answer()


@router.message(HabitState.waiting_for_fluid_name)
async def state_fluid_habit_name(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    if not message.text:
        return
    await state.update_data(habit_text=message.text.strip())

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n["habit_fluid_mode_brief_only"],
            callback_data=HabitFluidModeCallback(mode="brief_only").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=l10n["habit_fluid_mode_ask_time"],
            callback_data=HabitFluidModeCallback(mode="ask_time").pack(),
        )
    )
    await message.answer(l10n["habit_fluid_mode_prompt"], reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(HabitFluidModeCallback.filter())
async def cb_fluid_habit_mode(
    callback: CallbackQuery,
    callback_data: HabitFluidModeCallback,
    state: FSMContext,
    user: User,
    reminder_dao: ReminderDAO,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    habit_text = data.get("habit_text", l10n["habit_default_name"])
    mode = callback_data.mode

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
        logger.error("Validation error creating fluid habit: %s", ve)
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
            habit=habit_text,
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

    _parser = InputParser()
    result = await _parser.parse(message.text, user.timezone)

    if not result.parsed_datetime:
        await message.answer(l10n["habit_time_retry"])
        return

    execution_time_utc = to_utc_naive(result.parsed_datetime)

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
            l10n["habit_created"].format(habit=habit_text, time=time_str),
            parse_mode="Markdown",
        )
        await state.clear()
    except ValueError as ve:
        logger.error("Validation error creating habit: %s", ve)
        await message.answer(l10n["habit_create_failed_long"])
    except Exception as e:
        logger.error("Error creating habit: %s", e, exc_info=True)
        await reminder_dao.session.rollback()
        await message.answer(l10n["habit_create_failed_internal"])


@router.callback_query(F.data == "habit_list")
async def cb_habit_list(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO, l10n: dict[str, Any]
) -> None:
    reminders = await reminder_dao.get_user_reminders(user.id)
    habits = [r for r in reminders if r.status == "pending" and is_habit_entry(r)]

    if not habits:
        await callback.message.edit_text(
            l10n["habit_no_active"],
            reply_markup=get_habits_keyboard(l10n).as_markup(),
            parse_mode="Markdown",
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    text_lines = [l10n["habit_list_header"]]

    for i, h in enumerate(habits, start=1):
        streak, best = _habit_streak_labels(h)
        time_str = (
            l10n.get("habit_fluid_time_anytime", "anytime")
            if h.is_fluid_habit
            else format_time(h.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        )
        text_lines.append(
            l10n["habit_list_item"].format(
                index=i,
                habit=h.reminder_text,
                time=time_str,
                streak=streak,
                best=best,
                mode=_fluid_mode_label(h, l10n),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_task_settings", "⚙️"),
                callback_data=TaskSettingsCallback(reminder_id=h.id).pack(),
            ),
            InlineKeyboardButton(
                text=l10n["habit_btn_delete_n"].format(index=i),
                callback_data=DelHabitCallback(reminder_id=h.id).pack(),
            ),
        )

    builder.row(InlineKeyboardButton(text=l10n["habit_btn_back_dashboard"], callback_data="habit_back_dash"))

    await callback.message.edit_text(
        "\n".join(text_lines),
        parse_mode="Markdown",
        reply_markup=builder.as_markup(),
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
        text, reply_markup=get_habits_keyboard(l10n).as_markup(), parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(FluidDoneCallback.filter())
async def cb_fluid_done(
    callback: CallbackQuery,
    callback_data: FluidDoneCallback,
    reminder_dao: ReminderDAO,
    user: User,
    l10n: dict[str, Any],
) -> None:
    done = await reminder_dao.mark_fluid_habit_done_today(callback_data.reminder_id, user.timezone)
    if done:
        await callback.answer(l10n.get("fluid_done_saved", "✅ Marked as done for today."))
    else:
        await callback.answer(l10n.get("already_done", "Already done ✅"))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(FluidPickCustomCallback.filter())
async def cb_fluid_pick_custom(
    callback: CallbackQuery,
    callback_data: FluidPickCustomCallback,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    await state.update_data(fluid_pick_reminder_id=callback_data.reminder_id)
    await state.set_state(HabitState.waiting_for_fluid_time)
    await callback.message.answer(l10n["fluid_time_custom_prompt"], parse_mode="Markdown")
    await callback.answer()


@router.callback_query(FluidPickTimeCallback.filter())
async def cb_fluid_pick_time(
    callback: CallbackQuery,
    callback_data: FluidPickTimeCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    hhmm = f"{callback_data.hhmm[:2]}:{callback_data.hhmm[2:]}"

    reminder = await reminder_dao.get_by_id(callback_data.reminder_id)
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
        logger.error("Failed to schedule fluid habit %s: %s", callback_data.reminder_id, e, exc_info=True)
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

    reminder = await reminder_dao.get_by_id(int(reminder_id))
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


@router.callback_query(DelHabitCallback.filter())
async def cb_del_habit(
    callback: CallbackQuery,
    callback_data: DelHabitCallback,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    try:
        await reminder_dao.delete_by_id(callback_data.reminder_id)
        scheduler_service.remove_reminder_job(callback_data.reminder_id)
        await callback.answer(l10n["habit_deleted_alert"], show_alert=True)
        await callback.message.delete()
        await callback.message.answer(l10n["habit_deleted_followup"], parse_mode="Markdown")
    except Exception as e:
        logger.error("Error deleting habit %s: %s", callback_data.reminder_id, e)
        await callback.answer(l10n["habit_delete_error_alert"], show_alert=True)
