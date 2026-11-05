"""Habits handler — true daily custom habits builder."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import OperationalError

from bot.database.dao.errors import user_message
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import ReminderKind, User
from bot.database.models import (
    is_habit_like as _is_habit_like,  # noqa: F401 — re-exported; see test_is_habit_like_not_duplicated.py
)
from bot.handlers.reminders import (
    _UNDO_DELETE_WINDOW,
    _message_task_key,
    _parse_id_suffix,
    _remove_keyboard_after_delay,
    _soft_delete_reminder,
    active_auto_delete_tasks,
)
from bot.keyboards.inline import get_undo_delete_keyboard
from bot.services.habit_pause import apply_pause, is_paused, valid_pause_date
from bot.services.parser import InputParser
from bot.services.scheduler import SchedulerService
from bot.utils.markdown import escape_markdown
from bot.utils.pagination import preview_line
from bot.utils.telegram import safe_edit_text
from bot.utils.time_ext import (
    format_time,
    is_clock_input,
    local_time_on_date,
    local_time_today_strict,
    next_occurrence_utc,
    to_utc_aware,
    to_utc_naive,
)
from bot.utils.timezone import resolve_timezone

router = Router(name="habits")
logger = logging.getLogger(__name__)


class HabitState(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()
    confirming_time = State()
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
        avg_score=stats.get("avg_score", 0),
    )


async def _schedule_fluid_habit_for_today(
    *,
    reminder,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    hhmm: str,
) -> None:
    try:
        user_tz = resolve_timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC

    now_local = datetime.now(user_tz)
    hour, minute = hhmm.split(":", 1)
    # 1.3: DST-safe — see local_time_today_strict's docstring for why this
    # replaced a plain now_local.replace(hour=..., minute=...).
    target_utc_aware = local_time_today_strict(user.timezone, int(hour), int(minute))
    if target_utc_aware is None:
        raise ValueError("past_time")

    target_utc_naive = to_utc_naive(target_utc_aware)
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
        await reminder_dao.create_reminder(
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
        # create_reminder rejects on quota or text length; user_message picks
        # the localized wording for whichever fired (B-13).
        logger.error("Validation error: %s", ve)
        await callback.message.answer(user_message(ve, l10n))
        await callback.answer()
        return
    except Exception as e:
        logger.error("Error creating fluid habit: %s", e, exc_info=True)
        await reminder_dao.session.rollback()
        await callback.message.answer(l10n["habit_create_failed_internal"])
        await callback.answer()
        return

    # 1.3: commit BEFORE replying "created", not after (DatabaseMiddleware's
    # own implicit commit once this handler returns) — otherwise the user
    # can see "created" while the row never actually became durable.
    try:
        await reminder_dao.session.commit()
    except Exception as e:
        logger.error("Failed to commit fluid habit for user %s: %s", user.id, e, exc_info=True)
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

    parser = InputParser()
    try:
        result = await parser.parse(message.text, user.timezone, getattr(user, "language", None))
    except OperationalError as e:
        logger.error("DB locked parsing habit time input for user %s: %s", user.id, e)
        await message.answer(l10n.get("db_busy", "⏳ The database is busy — please try again in a few seconds."))
        return
    except Exception as e:
        logger.error(
            "Parser raised %s on habit time input for user %s (input_len=%d)",
            type(e).__name__, user.id, len(message.text), exc_info=True,
        )
        await message.answer(l10n["parse_error"])
        return

    if not result.parsed_datetime:
        await message.answer(l10n["habit_time_retry"])
        return

    if getattr(result, "date_only", False):
        day = result.parsed_datetime.date().isoformat()
        await state.update_data(habit_start_date=day)
        await message.answer(l10n["habit_choose_hour"].format(date=day))
        return
    selected_time = result.parsed_datetime
    if data.get("habit_start_date") and is_clock_input(message.text):
        local = selected_time.astimezone(resolve_timezone(user.timezone)) if selected_time.tzinfo else selected_time
        selected_time = local_time_on_date(user.timezone, date.fromisoformat(data["habit_start_date"]), local.hour, local.minute)
    # U-09 (2026-11-04 audit): keep a weekly/weekday/monthly rule from the
    # phrase ("каждый понедельник в 9") instead of silently making it daily
    # — streaks and the sweeper follow the habit's own rule now (B-08).
    habit_rule = getattr(result, "rrule_string", None) or "FREQ=DAILY"
    if habit_rule != (data.get("habit_rrule") or "FREQ=DAILY"):
        await state.update_data(habit_rrule=habit_rule)
    selected_time = _snap_to_rule(habit_rule, selected_time, user.timezone)
    needs_confirmation = (
        float(getattr(result,"confidence",1)) < 0.7
        or getattr(result,"rrule_string",None) not in {None,"FREQ=DAILY"}
        or (not is_clock_input(message.text) and not data.get("habit_start_date") and not getattr(result,"rrule_string",None))
    )
    if needs_confirmation:
        from bot.services.ui_context import create_context
        context = await create_context(reminder_dao.session,user.id,"habit_create",{})
        await state.update_data(habit_pending_time=selected_time.isoformat(),habit_confirm_token=context.token,habit_start_date=selected_time.astimezone(resolve_timezone(user.timezone)).date().isoformat())
        await state.set_state(HabitState.confirming_time)
        builder = InlineKeyboardBuilder()
        builder.button(text=l10n["confirm_save"], callback_data=f"habit_time_confirm:{context.token}")
        builder.button(text=l10n["btn_edit_time"], callback_data="habit_time_change")
        builder.button(text=l10n["btn_cancel"], callback_data="habit_cancel")
        when = format_time(selected_time, user.timezone, fmt="%d.%m.%Y %H:%M")
        if habit_rule == "FREQ=DAILY":
            prompt = l10n["habit_confirm_time"].format(time=when)
        else:
            prompt = l10n["habit_confirm_time_rule"].format(rule=_rule_label(habit_rule, l10n), time=when)
        await message.answer(prompt, reply_markup=builder.as_markup())
        return
    await _create_fixed_habit(message, state, user, reminder_dao, scheduler_service, l10n, selected_time)


@router.callback_query(HabitState.confirming_time, F.data.startswith("habit_time_confirm:"))
async def confirm_habit_time(callback: CallbackQuery, state: FSMContext, user: User, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]) -> None:
    data = await state.get_data()
    if not data.get("habit_pending_time") or callback.data != f"habit_time_confirm:{data.get('habit_confirm_token')}":
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    from sqlalchemy import update

    from bot.database.models import UiContext
    from bot.services.ui_context import now_utc
    claimed = await reminder_dao.session.execute(update(UiContext).where(UiContext.token==data.get("habit_confirm_token"),UiContext.user_id==user.id,UiContext.kind=="habit_create",UiContext.expires_at>now_utc(),UiContext.result.is_(None)).values(result="applied"))
    if claimed.rowcount != 1:
        await reminder_dao.session.rollback()
        return await callback.answer(l10n["invalid_action"],show_alert=True)
    await _create_fixed_habit(callback.message, state, user, reminder_dao, scheduler_service, l10n, datetime.fromisoformat(data["habit_pending_time"]))
    await callback.answer()


def _rule_label(rule: str, l10n: dict[str, Any]) -> str:
    from types import SimpleNamespace

    from bot.handlers.reminders_shared import _rrule_text

    return _rrule_text(SimpleNamespace(is_recurring=True, rrule_string=rule), l10n)


def _snap_to_rule(rule: str, selected_time: datetime, user_tz: str) -> datetime:
    """First occurrence of *rule* at or after *selected_time* (same clock
    time) — "every Monday at 9" typed on a Thursday starts next Monday."""
    if rule == "FREQ=DAILY":
        return selected_time
    start = to_utc_naive(selected_time)
    try:
        snapped = next_occurrence_utc(rule, start, user_tz, start - timedelta(seconds=1))
    except (ValueError, TypeError):
        return selected_time
    return to_utc_aware(snapped) if snapped else selected_time


async def _create_fixed_habit(message, state, user, reminder_dao, scheduler_service, l10n, selected_time):
    data = await state.get_data()
    habit_text = data.get("habit_text", l10n["habit_default_name"])
    habit_rule = data.get("habit_rrule") or "FREQ=DAILY"
    # Normalize to UTC once and store as naive UTC in DB.
    execution_time_utc = to_utc_naive(_snap_to_rule(habit_rule, selected_time, user.timezone))

    # The parser can return an ambiguous/past time (e.g. "at 9" when it's
    # already past 9 today in the user's zone). Since this habit is always
    # FREQ=DAILY, just advance to the next daily slot instead of creating a
    # reminder whose date-job would fire the instant it's scheduled.
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if execution_time_utc <= now_utc_naive:
        advanced = next_occurrence_utc(habit_rule, execution_time_utc, user.timezone, now_utc_naive)
        execution_time_utc = advanced or (execution_time_utc + timedelta(days=1))

    # Schedule it as a strict daily recurring task with nagging enabled
    try:
        reminder = await reminder_dao.create_reminder(
            user_id=user.id,
            text=habit_text,
            execution_time=execution_time_utc,
            is_recurring=True,
            rrule_string=habit_rule,
            is_habit=True,
            is_nagging=False,
            nagging_max_repeats=3,
        )
        scheduler_service.schedule_reminder(
            reminder.id,
            to_utc_aware(execution_time_utc),
            is_nagging=reminder.is_nagging,
        )
    except ValueError as ve:
        # See cb_fluid_habit_mode above (localized via user_message, B-13).
        await reminder_dao.session.rollback()
        logger.error("Habit validation rejected for user %s", user.id)
        await message.answer(user_message(ve, l10n))
        return
    except Exception as e:
        logger.error("Error creating habit: %s", e, exc_info=True)
        # Keep DB and scheduler state consistent if scheduling fails mid-flow.
        await reminder_dao.session.rollback()
        await message.answer(l10n["habit_create_failed_internal"])
        return

    # 1.3: commit BEFORE replying "created" (DatabaseMiddleware's own
    # implicit commit only runs once this handler returns) — otherwise the
    # user can see "created" while the row/job never actually persisted.
    try:
        await reminder_dao.session.commit()
    except Exception as e:
        logger.error("Failed to commit habit for user %s: %s", user.id, e, exc_info=True)
        await reminder_dao.session.rollback()
        scheduler_service.remove_reminder_job(reminder.id)
        await message.answer(l10n["habit_create_failed_internal"])
        return

    time_str = format_time(execution_time_utc, user.timezone, user.show_utc_offset, "%d.%m.%Y %H:%M")
    if habit_rule == "FREQ=DAILY":
        reply_text = l10n["habit_created"].format(habit=escape_markdown(habit_text), time=time_str)
    else:
        reply_text = l10n["habit_created_rule"].format(
            habit=escape_markdown(habit_text), rule=escape_markdown(_rule_label(habit_rule, l10n)), time=time_str
        )

    # 3.6: soft, one-time nudge on the 11th active habit — not a hard
    # limit (Streaks caps at 12), just a heads-up that most people don't
    # sustain more than ten at once. get_habit_motivation_stats'
    # active_count already covers both fixed and fluid habits (is_habit
    # is True for both), so this one check catches the threshold exactly
    # once, right as it's crossed, instead of nagging on every habit
    # created after it.
    stats = await reminder_dao.get_habit_motivation_stats(user.id, user.timezone, days=7)
    if stats.get("active_count") == 11:
        reply_text = f"{reply_text}\n\n{l10n.get('habit_overload_hint', '')}"

    await message.answer(reply_text, parse_mode="Markdown")
    await state.clear()

@router.callback_query(F.data == "habit_list")
async def open_habit_list(callback: CallbackQuery, user: User, reminder_dao: ReminderDAO, l10n: dict[str, Any]) -> None:
    from bot.handlers.experience import open_list
    await open_list(callback.message,reminder_dao,user,l10n,kind="habits",edit=True)
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
    # 2.1: "Back to dashboard" landing on the identical dashboard content
    # (no habit activity since it was last shown) is the ordinary case.
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_habits_keyboard(l10n).as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


# --- Step 13 (2026-09-26 audit remediation): pause a habit until a date -------

def _pause_choices(user_tz: str) -> list[tuple[str, date]]:
    """(label key, date) presets: next Monday, +1 week, +2 weeks."""
    try:
        today = datetime.now(resolve_timezone(user_tz)).date()
    except Exception:
        today = datetime.now(pytz.UTC).date()
    next_monday = today + timedelta(days=7 - today.weekday())
    return [
        ("habit_pause_until_monday", next_monday),
        ("habit_pause_1_week", today + timedelta(days=7)),
        ("habit_pause_2_weeks", today + timedelta(days=14)),
    ]


@router.callback_query(F.data.startswith("habit_pause_set_"))
async def cb_habit_pause_set(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService, l10n: dict[str, Any], state: FSMContext | None = None,
) -> None:
    try:
        raw_id, raw_date = callback.data[len("habit_pause_set_"):].split("_", 1)
        habit_id, until = int(raw_id), date.fromisoformat(raw_date)
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    habit = await reminder_dao.get_owned(habit_id, user.id)
    if not habit or not habit.is_habit:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    if not valid_pause_date(until, user.timezone):
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    if not await apply_pause(habit, user.timezone, scheduler_service, reminder_dao.session, until):
        return await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
    await callback.answer(l10n.get("habit_paused_until", "⏸ Paused until {date}").format(date=until.strftime("%d.%m")), show_alert=True)
    await _refresh_habit_origin(callback,state,user,reminder_dao,l10n)


@router.callback_query(F.data.startswith("habit_pause_clear_"))
async def cb_habit_pause_clear(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService, l10n: dict[str, Any], state: FSMContext | None = None,
) -> None:
    try:
        habit_id = int(callback.data[len("habit_pause_clear_"):])
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    habit = await reminder_dao.get_owned(habit_id, user.id)
    if not habit or not habit.is_habit:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    if not await apply_pause(habit, user.timezone, scheduler_service, reminder_dao.session, None):
        return await callback.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."), show_alert=True)
    await callback.answer(l10n.get("habit_pause_cleared", "▶️ Habit resumed"), show_alert=True)
    await _refresh_habit_origin(callback,state,user,reminder_dao,l10n)


@router.callback_query(F.data.startswith("habit_pause_"))
async def cb_habit_pause_menu(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO, l10n: dict[str, Any], state: FSMContext | None = None,
) -> None:
    """Registered AFTER habit_pause_set_/habit_pause_clear_ — this prefix
    filter also matches those, and aiogram tries handlers in order."""
    try:
        habit_id = int(callback.data[len("habit_pause_"):])
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    habit = await reminder_dao.get_owned(habit_id, user.id)
    if not habit or not habit.is_habit:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    builder = InlineKeyboardBuilder()
    for key, until in _pause_choices(user.timezone):
        builder.row(InlineKeyboardButton(
            text=l10n.get(key, key).format(date=until.strftime("%d.%m")),
            callback_data=f"habit_pause_set_{habit.id}_{until.isoformat()}",
        ))
    if is_paused(habit):
        builder.row(InlineKeyboardButton(
            text=l10n.get("habit_pause_clear", "▶️ Resume now"),
            callback_data=f"habit_pause_clear_{habit.id}",
        ))
    data=await state.get_data() if state is not None else {}
    back=f"nav:{data['nav_token']}:{data.get('nav_page',0)}" if data.get("nav_token") else "habit_list"
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_back_dashboard"], callback_data=back))
    await safe_edit_text(
        callback.message,
        l10n.get("habit_pause_prompt", "⏸ Pause «{habit}» until:").format(habit=escape_markdown(preview_line(habit.reminder_text))),
        reply_markup=builder.as_markup(),
        parse_mode="Markdown",
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
                tz = resolve_timezone(user.timezone)
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

    reminder = await reminder_dao.get_owned(reminder_id, user.id)
    kind = reminder.kind if reminder else ReminderKind.TASK
    is_fluid = kind == ReminderKind.FLUID_HABIT
    if not reminder or kind == ReminderKind.TASK:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return

    record_kwargs: dict[str, Any] = {}
    if is_fluid:
        try:
            tz = resolve_timezone(user.timezone)
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
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    """Soft-delete a habit with an undo window.

    Used to hard-delete the reminder AND its whole habit_events history
    (streaks, weekly/monthly report data) immediately, with no confirmation
    and no way back — a mistap on the delete button, which sits right next
    to the settings gear in the habits list, was permanent. Reuses the same
    pending_delete_at + undo-window mechanism reminders.py's del_task_
    already uses for plain tasks (and, incidentally, for fixed habits
    reachable via "My Tasks" — get_user_reminders doesn't exclude
    is_habit=True rows, only is_fluid_habit ones). delete_cleanup.py's
    sweep hard-deletes both the reminder and its habit_events once the
    undo window elapses, restart-safe by construction.
    """
    task_id = _parse_id_suffix(callback.data, "del_habit_")
    if task_id is None:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return
    reminder = await reminder_dao.get_owned(task_id, user.id)
    if not reminder:
        await callback.answer(l10n["item_not_found"], show_alert=True)
        return
    try:
        # A-14: commit pending_delete_at BEFORE tearing down the scheduler
        # job — see reminders_shared._soft_delete_reminder's docstring for
        # why the old (job-removal-first) order could leave a habit with
        # no job at all if the commit then failed.
        if not await _soft_delete_reminder(reminder, reminder_dao, scheduler_service):
            await callback.answer(l10n["habit_delete_error_alert"], show_alert=True)
            return

        await callback.answer(l10n["habit_deleted_alert"], show_alert=True)
        await callback.message.edit_text(
            l10n["habit_deleted_followup"],
            reply_markup=get_undo_delete_keyboard(task_id, l10n),
            parse_mode="Markdown",
        )
        task = asyncio.create_task(_remove_keyboard_after_delay(callback.message, _UNDO_DELETE_WINDOW))
        active_auto_delete_tasks[_message_task_key(callback.message)] = task
    except Exception as e:
        logger.error("Error deleting habit %s: %s", task_id, e)
        await callback.answer(l10n["habit_delete_error_alert"], show_alert=True)


@router.callback_query(HabitState.confirming_time,F.data == "habit_time_change")
async def change_habit_time(callback: CallbackQuery,state: FSMContext,l10n: dict[str,Any]) -> None:
    await state.set_state(HabitState.waiting_for_time)
    await callback.message.answer(l10n["habit_time_prompt"])
    await callback.answer()


@router.message(HabitState.confirming_time,F.text,~F.text.startswith("/"))
async def retype_habit_time(message: Message,state: FSMContext,user: User,reminder_dao: ReminderDAO,scheduler_service: SchedulerService,l10n: dict[str,Any]) -> None:
    await state.set_state(HabitState.waiting_for_time)
    await state_habit_time(message,state,user,reminder_dao,scheduler_service,l10n)


async def _refresh_habit_origin(callback,state,user,dao,l10n):
    from bot.handlers.experience import open_list, render_list
    from bot.services.ui_context import get_context
    data=await state.get_data() if state is not None else {}
    context=await get_context(dao.session,data.get("nav_token"),user.id,"list") if data.get("nav_token") else None
    if context:
        await render_list(callback.message,dao,user,l10n,context,data.get("nav_page",0))
    else:
        await open_list(callback.message,dao,user,l10n,kind="habits",edit=True)
