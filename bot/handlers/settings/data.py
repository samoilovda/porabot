""""Данные и интеграции" settings group (step 9, 2026-09-26 audit
remediation): data export, the read-only .ics calendar feed, and the full
account reset ("Clear all") — split out of the original
bot/handlers/settings.py, see the package's __init__.py docstring for the
full module family and why.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.exc import OperationalError

from bot.config import config
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import (
    get_clear_all_confirm_keyboard,
    get_data_group_keyboard,
    get_ics_feed_keyboard,
    get_language_selection_keyboard,
)
from bot.services.scheduler import SchedulerService
from bot.services.user_data import UserDataService

router = Router(name="settings_data")
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "settings_group_data")
async def callback_settings_group_data(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    """Step 9: entry point into this group from the top-level Settings
    screen — same settings text, just the group's own keyboard overlaid."""
    await callback.message.edit_reply_markup(reply_markup=get_data_group_keyboard(l10n))
    await callback.answer()


def _dt_iso(value) -> Any:
    return value.isoformat() if value else None


def build_data_export(user: User, reminders, habit_events) -> dict:
    """Build the JSON-serializable payload for the 3.3 data export: user
    settings + reminders + habit_events. Only excludes reminders currently
    inside the undo-delete window (pending_delete_at set) — those are about
    to be purged and aren't meaningfully "the user's data" any more."""
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "id": user.id,
            "timezone": user.timezone,
            "language": user.language,
            "show_utc_offset": bool(getattr(user, "show_utc_offset", False)),
            "quiet_hours_enabled": bool(getattr(user, "quiet_hours_enabled", False)),
            "quiet_hours_start": getattr(user, "quiet_hours_start", None),
            "quiet_hours_end": getattr(user, "quiet_hours_end", None),
            "quiet_hours_weekend_enabled": bool(getattr(user, "quiet_hours_weekend_enabled", False)),
            "quiet_hours_weekend_start": getattr(user, "quiet_hours_weekend_start", None),
            "quiet_hours_weekend_end": getattr(user, "quiet_hours_weekend_end", None),
            "quiet_hours_habits_exempt": bool(getattr(user, "quiet_hours_habits_exempt", False)),
            "briefs_enabled": bool(getattr(user, "briefs_enabled", True)),
            "morning_brief_time": getattr(user, "morning_brief_time", None),
            "evening_brief_time": getattr(user, "evening_brief_time", None),
            "missed_recovery_enabled": bool(getattr(user, "missed_recovery_enabled", True)),
            "missed_recovery_time": getattr(user, "missed_recovery_time", None),
            "habit_reports_enabled": bool(getattr(user, "habit_reports_enabled", True)),
            "habit_report_weekday": getattr(user, "habit_report_weekday", None),
            "habit_report_time": getattr(user, "habit_report_time", None),
        },
        "reminders": [
            {
                "id": r.id,
                "text": r.reminder_text,
                "execution_time": _dt_iso(r.execution_time),
                "is_recurring": r.is_recurring,
                "rrule_string": r.rrule_string,
                "tags": r.tags,
                "priority": r.priority,
                "is_habit": r.is_habit,
                "is_fluid_habit": r.is_fluid_habit,
                "fluid_mode": r.fluid_mode,
                "status": r.status,
                "is_nagging": r.is_nagging,
                "nagging_max_repeats": r.nagging_max_repeats,
                "habit_streak_current": r.habit_streak_current,
                "habit_streak_best": r.habit_streak_best,
                "fluid_streak_current": getattr(r, "fluid_streak_current", 0),
                "fluid_streak_best": getattr(r, "fluid_streak_best", 0),
                "completed_at": _dt_iso(r.completed_at),
                "created_at": _dt_iso(r.created_at),
            }
            for r in reminders
            if getattr(r, "pending_delete_at", None) is None
        ],
        "habit_events": [
            {
                "reminder_id": e.reminder_id,
                "habit_text": e.habit_text,
                "local_date": e.local_date,
                "due_at": _dt_iso(e.due_at),
                "outcome": e.outcome,
                "source": e.source,
                "created_at": _dt_iso(e.created_at),
            }
            for e in habit_events
        ],
    }


@router.callback_query(F.data == "settings_export_data")
async def callback_export_data(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    l10n: dict[str, Any],
) -> None:
    try:
        reminders = await reminder_dao.get_all(user_id=user.id)
        habit_events = await habit_event_dao.get_all(user_id=user.id)
        payload = build_data_export(user, reminders, habit_events)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        await callback.message.answer_document(
            BufferedInputFile(data, filename=f"porabot_export_{user.id}.json"),
            caption=l10n.get("export_data_caption", "📤 Here is your data export."),
        )
    except Exception as e:
        logger.error("Data export failed for user %s: %s", user.id, e, exc_info=True)
        await callback.answer(l10n.get("export_data_error", "❌ Failed to export data."), show_alert=True)
        return
    await callback.answer()


def _ics_feed_url(token: str) -> str:
    """4.4: build the shareable feed URL. Falls back to a localhost URL
    (not actually reachable outside the host) when PUBLIC_BASE_URL hasn't
    been configured yet — see bot/config.py's docstring on that setting."""
    base = config.PUBLIC_BASE_URL.rstrip("/") if config.PUBLIC_BASE_URL else f"http://localhost:{config.WEB_SERVER_PORT}"
    return f"{base}/ics/{token}.ics"


async def _render_ics_feed_screen(callback: CallbackQuery, token: str, l10n: dict[str, Any]) -> None:
    url = _ics_feed_url(token)
    text = l10n.get(
        "ics_feed_text",
        "📅 *Calendar feed*\n\nSubscribe to this link in Google/Apple Calendar to see your Porabot tasks there (read-only):\n\n`{url}`\n\nAnyone with this link can read your tasks — keep it private. You can generate a new one below, which invalidates this one.",
    ).format(url=url)
    await callback.message.edit_text(text, reply_markup=get_ics_feed_keyboard(l10n), parse_mode="Markdown")


@router.callback_query(F.data == "settings_ics_feed")
async def callback_ics_feed(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    token = await user_dao.ensure_ics_feed_token(user.id)
    await _render_ics_feed_screen(callback, token, l10n)
    await callback.answer()


@router.callback_query(F.data == "settings_ics_feed_regenerate")
async def callback_ics_feed_regenerate(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    token = await user_dao.regenerate_ics_feed_token(user.id)
    await _render_ics_feed_screen(callback, token, l10n)
    await callback.answer(l10n.get("ics_feed_regenerated", "🔄 New link generated — the old one no longer works."))


@router.callback_query(F.data == "settings_clear_all")
async def callback_clear_all_prompt(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n.get(
            "clear_all_confirm_text",
            "⚠️ This will delete all your tasks, habits, and settings permanently.\n\nAre you sure?",
        ),
        reply_markup=get_clear_all_confirm_keyboard(l10n),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_clear_all_confirm")
async def callback_clear_all_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    # "Clear all" is a data RESET, not account deletion: the users row
    # survives with default settings (so the user re-onboards), and
    # payments is never touched — see UserDataService's docstring.
    service = UserDataService(reminder_dao, habit_event_dao)
    try:
        reminder_ids = await service.reset(user)
    except OperationalError as e:
        logger.error("DB locked resetting data for user %s: %s", user.id, e)
        await callback.answer(
            l10n.get("db_busy", "⏳ The database is busy — please try again in a few seconds."),
            show_alert=True,
        )
        return

    # Only remove scheduler jobs once the reset actually committed — a
    # failed commit above must leave jobs and DB state consistent.
    for reminder_id in reminder_ids:
        scheduler_service.remove_reminder_job(reminder_id)

    await state.clear()

    await callback.message.edit_text(
        l10n.get("clear_all_done", "✅ All data deleted. Starting from scratch."),
        reply_markup=None,
    )
    await callback.message.answer(
        l10n["choose_language"],
        reply_markup=get_language_selection_keyboard(l10n),
    )
    await callback.answer()
