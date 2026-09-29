"""Settings handlers (step 9, 2026-09-26 audit remediation: split out of
the original 1191-line bot/handlers/settings.py into this package).

The top-level Settings screen groups everything into three screens
instead of one flat ~10-button list — "Уведомления" (notifications.py:
briefs, quiet hours, habit reports, missed-task recovery), "Язык и время"
(locale_time.py: timezone incl. the habit-migration prompt, language, and
the UTC-offset display toggle), "Данные и интеграции" (data.py: export,
calendar feed, Mini App, and full account reset). Every existing
callback_data string is unchanged — only which screen a button lives on,
and which module its handler lives in, moved.

Nothing outside this package should need to know it's a package rather
than one module: `router` and every name external code imports (see the
imports below) are re-exported here exactly as bot/handlers/settings.py
used to export them directly.
"""

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.database.models import User

# --- shared state/text-rendering helpers, re-exported for external callers
# (bot/handlers/menu.py imports _render_settings_text; several tests import
# SettingsState) ---
from bot.handlers.settings._common import (  # noqa: F401
    SettingsState,
    _format_tz_display_label,
    _quiet_hours_label,
    _render_settings_text,
)

# --- data: export/ICS feed/clear-all, re-exported for tests ---
from bot.handlers.settings.data import (  # noqa: F401
    _dt_iso,
    _ics_feed_url,
    _render_ics_feed_screen,
    build_data_export,
    callback_clear_all_confirm,
    callback_clear_all_prompt,
    callback_export_data,
    callback_ics_feed,
    callback_ics_feed_regenerate,
    callback_settings_group_data,
)
from bot.handlers.settings.data import router as _data_router

# --- locale_time: timezone/language/UTC-offset, re-exported for tests ---
from bot.handlers.settings.locale_time import (  # noqa: F401
    _HALF_HOUR_TZ_MAP,
    _MAX_TZMIG_SUMMARY_LINES,
    _load_tzmig_plan,
    _render_tz_migration_offer,
    _render_tz_migration_summary,
    _resolve_timezone_candidate,
    callback_change_lang,
    callback_change_tz,
    callback_set_tz,
    callback_settings_group_locale_time,
    callback_toggle_utc,
    callback_tzmig_all,
    callback_tzmig_apply,
    callback_tzmig_back,
    callback_tzmig_none,
    callback_tzmig_pick,
    callback_tzmig_toggle,
    cmd_language,
    cmd_timezone,
    state_set_manual_timezone,
)
from bot.handlers.settings.locale_time import router as _locale_time_router

# --- notifications: briefs/quiet hours/habit reports/missed recovery,
# re-exported for tests ---
from bot.handlers.settings.notifications import (  # noqa: F401
    _quiet_hours_kwargs,
    callback_briefs_edit_hour,
    callback_briefs_setup,
    callback_briefs_toggle,
    callback_habit_report_day_selected,
    callback_habit_report_edit_day,
    callback_habit_report_edit_time,
    callback_habit_reports_setup,
    callback_habit_reports_toggle,
    callback_missed_recovery_edit_time,
    callback_missed_recovery_setup,
    callback_missed_recovery_toggle,
    callback_quiet_edit_time,
    callback_quiet_habits_exempt_toggle,
    callback_quiet_setup,
    callback_quiet_toggle,
    callback_quiet_weekend_toggle,
    callback_settings_group_notifications,
    state_briefs_set_time,
    state_habit_report_set_time,
    state_missed_recovery_set_time,
    state_set_quiet_time,
)
from bot.handlers.settings.notifications import router as _notifications_router
from bot.keyboards.inline import get_settings_keyboard

router = Router(name="settings")
router.include_router(_locale_time_router)
router.include_router(_notifications_router)
router.include_router(_data_router)


@router.callback_query(F.data == "settings_back")
async def callback_settings_back(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    """Every settings-group screen's "🔙 Back" (get_notifications_group_
    keyboard/get_locale_time_group_keyboard/get_data_group_keyboard) and
    every individual setting screen's own "settings_back" button all land
    here — the one true top-level Settings screen, unchanged from before
    this package existed."""
    text = _render_settings_text(user, l10n)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")
    await callback.answer()
