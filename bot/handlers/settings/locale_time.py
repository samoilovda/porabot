""""Язык и время" settings group (step 9, 2026-09-26 audit remediation):
timezone (presets, manual offset entry, the habit-migration prompt that
follows a change), language, and the UTC-offset display toggle — split out
of the original bot/handlers/settings.py, see the package's __init__.py
docstring for the full module family and why.
"""

import logging
import re
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.handlers.settings._common import SettingsState, _format_tz_display_label
from bot.keyboards.inline import (
    get_language_selection_keyboard,
    get_locale_time_group_keyboard,
    get_timezone_keyboard,
    get_tz_migration_choice_keyboard,
    get_tz_migration_pick_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.scheduler import SchedulerService
from bot.services.tz_migration import (
    HabitTzMigrationItem,
    apply_migration,
    build_migration_plan,
    migratable_habits,
)
from bot.utils.markdown import escape_markdown

router = Router(name="settings_locale_time")
logger = logging.getLogger(__name__)

# Half/quarter-hour UTC offsets mapped to a real IANA zone that actually uses
# that offset today, so users in these regions get correct DST behavior
# instead of a frozen fixed offset. Not exhaustive — offsets with no matching
# well-known zone are rejected rather than silently approximated.
_HALF_HOUR_TZ_MAP: dict[str, str] = {
    "+3:30": "Asia/Tehran",
    "+4:30": "Asia/Kabul",
    "+5:30": "Asia/Kolkata",
    "+5:45": "Asia/Kathmandu",
    "+6:30": "Asia/Yangon",
    "+8:45": "Australia/Eucla",
    "+9:30": "Australia/Darwin",
    "+10:30": "Australia/Lord_Howe",
    "+12:45": "Pacific/Chatham",
    "-3:30": "America/St_Johns",
    "-9:30": "Pacific/Marquesas",
}


def _resolve_timezone_candidate(raw: str) -> str:
    """
    Normalize manual timezone input from UTC offset to a canonical IANA timezone.

    Supported manual formats:
      - +5, -6, 0                    → whole-hour offset
      - +5:30, -3:30, +5:45, etc.    → known half/quarter-hour offset (see
                                        _HALF_HOUR_TZ_MAP)

    Whole-hour offsets resolve to Etc/GMT±N, a *fixed* offset with no DST —
    accurate today, but a user in a DST-observing region will drift by an
    hour when their local clocks change. The keyboard presets (real IANA
    city zones) don't have this problem; manual entry is a deliberate
    trade-off for regions not covered by the preset list.

    Returns:
      - UTC for 0
      - Etc/GMT-5 for +5, Etc/GMT+6 for -6 (note the reversed IANA sign)
      - a real IANA zone for known half/quarter-hour offsets
    """
    candidate = (raw or "").strip().replace(",", ".")
    # Accept "+5.5" / "-3.5" as an alternate spelling of "+5:30" / "-3:30".
    half_match = re.fullmatch(r"([+-]?\d{1,2})\.5", candidate)
    if half_match:
        candidate = f"{half_match.group(1)}:30"
    if not candidate.startswith(("+", "-")) and ":" in candidate:
        candidate = f"+{candidate}"

    if ":" in candidate:
        mapped = _HALF_HOUR_TZ_MAP.get(candidate)
        if mapped is None:
            raise pytz.UnknownTimeZoneError(raw)
        return mapped

    if not re.fullmatch(r"[+-]?\d{1,2}", candidate):
        raise pytz.UnknownTimeZoneError(raw)

    hours = int(candidate)
    if hours < -12 or hours > 14:
        raise pytz.UnknownTimeZoneError(raw)

    if hours == 0:
        return "UTC"

    # NOTE: IANA Etc/GMT has reversed sign semantics by convention.
    sign = "-" if hours > 0 else "+"
    return f"Etc/GMT{sign}{abs(hours)}"


@router.callback_query(F.data == "settings_group_locale_time")
async def callback_settings_group_locale_time(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    """Step 9: entry point into this group from the top-level Settings
    screen — same settings text, just the group's own keyboard overlaid
    (same "text stays, keyboard changes" pattern every leaf screen below
    already used before this grouping existed)."""
    await callback.message.edit_reply_markup(
        reply_markup=get_locale_time_group_keyboard(l10n, user.show_utc_offset)
    )
    await callback.answer()


# --- Habit timezone migration ---------------------------------------------
# Offered right after a timezone change (settings_change_tz / manual entry),
# never during onboarding — a brand-new user has no habits yet.

_MAX_TZMIG_SUMMARY_LINES = 30


async def _render_tz_migration_offer(
    edit_fn,
    resend_menu_fn,
    *,
    user: User,
    old_tz: str,
    new_tz: str,
    reminder_dao: ReminderDAO,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    """Show either the plain tz-success text (nothing to migrate, or the
    timezone didn't actually change) or the migrate-all/pick/skip prompt.

    The two "nothing to migrate" branches are dead ends — no more buttons,
    the flow is over — and edit_fn is always an edit_text (or, for the
    manual-entry caller, a plain answer with no reply_markup): neither can
    attach a ReplyKeyboardMarkup. If the user collapsed the persistent
    bottom menu to type a timezone offset, nothing would ever bring it
    back without resend_menu_fn re-sending it as a small separate message.
    """
    if old_tz == new_tz:
        await edit_fn(l10n["tz_success"].format(tz=_format_tz_display_label(new_tz)), None)
        await resend_menu_fn()
        return

    habits = await reminder_dao.get_active_habits(user.id)
    candidates = migratable_habits(habits, old_tz)
    if not candidates:
        await edit_fn(l10n["tz_success"].format(tz=_format_tz_display_label(new_tz)), None)
        await resend_menu_fn()
        return

    sample = build_migration_plan(candidates[:1], old_tz, new_tz)[0]
    await state.update_data(tzmig_old_tz=old_tz, tzmig_new_tz=new_tz, tzmig_selected=None)
    text = l10n.get(
        "tz_migrate_prompt",
        "✅ Timezone: `{tz}`\n\nYou have {count} habit(s) with a fixed time. Left as is, they'll fire at a "
        "different local time (e.g. {old} → {drift}).\n\nMigrate them to the new timezone?",
    ).format(
        tz=_format_tz_display_label(new_tz),
        count=len(candidates),
        old=sample.old_local_hhmm,
        drift=sample.drift_local_hhmm,
    )
    await edit_fn(text, get_tz_migration_choice_keyboard(l10n))


def _format_tzmig_lines(items: list[HabitTzMigrationItem], line_fmt, l10n: dict[str, Any]) -> str:
    shown = items[:_MAX_TZMIG_SUMMARY_LINES]
    lines = [line_fmt(item) for item in shown]
    if len(items) > _MAX_TZMIG_SUMMARY_LINES:
        lines.append(
            l10n.get("tz_migrate_summary_more", "… and {count} more").format(
                count=len(items) - _MAX_TZMIG_SUMMARY_LINES
            )
        )
    return "\n".join(lines)


def _render_tz_migration_summary(
    migrated: list[HabitTzMigrationItem],
    kept: list[HabitTzMigrationItem],
    new_tz: str,
    l10n: dict[str, Any],
) -> str:
    title = l10n.get("tz_migrate_summary_title", "✅ Timezone: `{tz}`").format(tz=_format_tz_display_label(new_tz))
    if not migrated and not kept:
        return title

    parts = [title]
    if migrated:
        lines = _format_tzmig_lines(
            migrated,
            lambda item: l10n.get("tz_migrate_summary_migrated_line", "• {habit} — still {time}").format(
                habit=escape_markdown(item.text), time=item.old_local_hhmm
            ),
            l10n,
        )
        parts.append(
            l10n.get("tz_migrate_summary_migrated", "\n\n*Migrated ({count}):*\n{lines}").format(
                count=len(migrated), lines=lines
            )
        )
    if kept:
        lines = _format_tzmig_lines(
            kept,
            lambda item: l10n.get(
                "tz_migrate_summary_kept_line", "• {habit} — will fire at {time} instead of {old_time}"
            ).format(habit=escape_markdown(item.text), time=item.drift_local_hhmm, old_time=item.old_local_hhmm),
            l10n,
        )
        parts.append(
            l10n.get("tz_migrate_summary_kept", "\n\n*Left as is ({count}):*\n{lines}").format(
                count=len(kept), lines=lines
            )
        )
    return "".join(parts)


async def _load_tzmig_plan(
    user: User, reminder_dao: ReminderDAO, old_tz: str, new_tz: str
) -> list[HabitTzMigrationItem]:
    """Recompute the migration plan fresh from the DB — nothing but the
    old/new tz strings and the current selection is kept in FSM state, so
    every step reflects any habit changes made mid-flow."""
    habits = await reminder_dao.get_active_habits(user.id)
    candidates = migratable_habits(habits, old_tz)
    return build_migration_plan(candidates, old_tz, new_tz)


@router.callback_query(F.data == "tzmig_all")
async def callback_tzmig_all(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return

    items = await _load_tzmig_plan(user, reminder_dao, old_tz, new_tz)
    selected_ids = {item.reminder_id for item in items}
    migrated, kept = await apply_migration(items, selected_ids, reminder_dao, scheduler_service, new_tz)
    await state.update_data(tzmig_old_tz=None, tzmig_new_tz=None, tzmig_selected=None)
    await callback.message.edit_text(_render_tz_migration_summary(migrated, kept, new_tz, l10n), reply_markup=None)
    # Terminal screen — edit_text can't carry a ReplyKeyboardMarkup, so
    # resend the persistent bottom menu as its own small message in case
    # the user collapsed it while typing the timezone.
    await callback.message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data == "tzmig_none")
async def callback_tzmig_none(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return

    items = await _load_tzmig_plan(user, reminder_dao, old_tz, new_tz)
    await state.update_data(tzmig_old_tz=None, tzmig_new_tz=None, tzmig_selected=None)
    await callback.message.edit_text(_render_tz_migration_summary([], items, new_tz, l10n), reply_markup=None)
    await callback.message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data == "tzmig_pick")
async def callback_tzmig_pick(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return

    items = await _load_tzmig_plan(user, reminder_dao, old_tz, new_tz)
    selected = data.get("tzmig_selected")
    if selected is None:
        selected = [item.reminder_id for item in items]
        await state.update_data(tzmig_selected=selected)

    await callback.message.edit_text(
        l10n.get(
            "tz_migrate_pick_intro",
            "Tick the habits to migrate to the new timezone (⬜ = leave as is), then tap Apply.",
        ),
        reply_markup=get_tz_migration_pick_keyboard(items, set(selected), l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tzmig_toggle_"))
async def callback_tzmig_toggle(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return
    try:
        reminder_id = int(callback.data.split("tzmig_toggle_")[1])
    except ValueError:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    selected = set(data.get("tzmig_selected") or [])
    if reminder_id in selected:
        selected.discard(reminder_id)
    else:
        selected.add(reminder_id)
    await state.update_data(tzmig_selected=list(selected))

    items = await _load_tzmig_plan(user, reminder_dao, old_tz, new_tz)
    await callback.message.edit_reply_markup(reply_markup=get_tz_migration_pick_keyboard(items, selected, l10n))
    await callback.answer()


@router.callback_query(F.data == "tzmig_back")
async def callback_tzmig_back(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return

    async def _editor(text: str, markup) -> None:
        await callback.message.edit_text(text, reply_markup=markup)

    async def _resend_menu() -> None:
        await callback.message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))

    await _render_tz_migration_offer(
        _editor, _resend_menu, user=user, old_tz=old_tz, new_tz=new_tz, reminder_dao=reminder_dao, state=state, l10n=l10n
    )
    await callback.answer()


@router.callback_query(F.data == "tzmig_apply")
async def callback_tzmig_apply(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    state: FSMContext,
    l10n: dict[str, Any],
) -> None:
    data = await state.get_data()
    old_tz, new_tz = data.get("tzmig_old_tz"), data.get("tzmig_new_tz")
    if not old_tz or not new_tz:
        await callback.answer(l10n.get("invalid_action", "❌ Invalid action"), show_alert=True)
        return

    items = await _load_tzmig_plan(user, reminder_dao, old_tz, new_tz)
    selected_ids = set(data.get("tzmig_selected") or [])
    migrated, kept = await apply_migration(items, selected_ids, reminder_dao, scheduler_service, new_tz)
    await state.update_data(tzmig_old_tz=None, tzmig_new_tz=None, tzmig_selected=None)
    await callback.message.edit_text(_render_tz_migration_summary(migrated, kept, new_tz, l10n), reply_markup=None)
    await callback.message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data == "settings_toggle_utc")
async def callback_toggle_utc(callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any]) -> None:
    new_val = not user.show_utc_offset
    await user_dao.update_show_utc_offset(user.id, new_val)
    # Step 9: this button now lives inside the locale/time GROUP screen
    # (get_locale_time_group_keyboard), not the top-level settings
    # keyboard — re-render that same group screen, not the top level.
    await callback.message.edit_reply_markup(reply_markup=get_locale_time_group_keyboard(l10n, new_val))
    await callback.answer()


@router.callback_query(F.data == "settings_change_tz")
async def callback_change_tz(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_tz"], reply_markup=get_timezone_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data == "settings_change_lang")
async def callback_change_lang(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))
    await callback.answer()


# /timezone and /language: direct shortcuts to the two settings sub-menus a
# user is most likely to need urgently (wrong reminder times, wrong UI
# language) when the reply-keyboard footer that normally leads to Settings
# has failed to render. Reuse the same keyboards/callbacks as
# settings_change_tz/settings_change_lang above — set_tz_*/set_lang_*
# already handle being triggered outside onboarding via state_data's
# onboarding_timezone flag, which state.clear() here guarantees is unset.
@router.message(Command("timezone"))
async def cmd_timezone(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await message.answer(l10n["choose_tz"], reply_markup=get_timezone_keyboard(l10n))


@router.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await message.answer(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))


@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(
    callback: CallbackQuery,
    user_dao: UserDAO,
    user: User,
    l10n: dict[str, Any],
    state: FSMContext,
    reminder_dao: ReminderDAO,
) -> None:
    state_data = await state.get_data()
    is_onboarding_tz = bool(state_data.get("onboarding_timezone"))
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await state.set_state(SettingsState.waiting_for_timezone)
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        await callback.answer()
        return

    # 32: callback_data is client-controlled — Telegram doesn't cryptographically
    # bind it to the keyboard actually shown, so a forged "set_tz_<garbage>"
    # must not persist an unparseable zone. A stored invalid timezone later
    # makes pytz.timezone() raise deep inside InputParser._parse_sync()
    # instead of returning a parse result, breaking reminder creation for
    # that user until they fix it via manual entry.
    try:
        pytz.timezone(action)
    except pytz.UnknownTimeZoneError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)

    old_tz = user.timezone
    await user_dao.update_timezone(user.id, action)
    user.timezone = action

    if is_onboarding_tz:
        await callback.message.edit_text(
            l10n["tz_success"].format(tz=_format_tz_display_label(action)),
            reply_markup=None,
        )
        await state.clear()
        text = l10n["cmd_start"].format(name=escape_markdown(callback.from_user.first_name))
        await callback.message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
        await callback.message.answer(l10n["onboarding_example_hint"])
        await callback.answer()
        return

    async def _editor(text: str, markup) -> None:
        await callback.message.edit_text(text, reply_markup=markup)

    async def _resend_menu() -> None:
        await callback.message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))

    await _render_tz_migration_offer(
        _editor, _resend_menu, user=user, old_tz=old_tz, new_tz=action, reminder_dao=reminder_dao, state=state, l10n=l10n
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_timezone, F.text)
async def state_set_manual_timezone(
    message: Message,
    state: FSMContext,
    user: User,
    user_dao: UserDAO,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
) -> None:
    tz_candidate = message.text.strip()
    try:
        resolved_tz = _resolve_timezone_candidate(tz_candidate)
    except pytz.UnknownTimeZoneError:
        await message.answer(
            l10n.get(
                "tz_invalid",
                "❌ Invalid timezone offset. Use `+5`, `0`, or `-6`.",
            ),
            parse_mode="Markdown",
        )
        return

    state_data = await state.get_data()
    is_onboarding_tz = bool(state_data.get("onboarding_timezone"))

    old_tz = user.timezone
    await user_dao.update_timezone(user.id, resolved_tz)
    user.timezone = resolved_tz
    await state.clear()

    if is_onboarding_tz:
        await message.answer(
            l10n["tz_success"].format(tz=_format_tz_display_label(resolved_tz)),
            parse_mode="Markdown",
        )
        text = l10n["cmd_start"].format(name=escape_markdown(message.from_user.first_name))
        await message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
        await message.answer(l10n["onboarding_example_hint"])
        return

    async def _editor(text: str, markup) -> None:
        await message.answer(text, reply_markup=markup)

    async def _resend_menu() -> None:
        await message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))

    await _render_tz_migration_offer(
        _editor, _resend_menu, user=user, old_tz=old_tz, new_tz=resolved_tz, reminder_dao=reminder_dao, state=state, l10n=l10n
    )
