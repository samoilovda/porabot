"""Reminder Handlers — composition root for the bot.handlers.reminders_*
module family (4.1: split out of what used to be one 2300-line file).

FSM states (ReminderWizard):
  entering_text  → waiting for reminder text
  choosing_time  → showing time-selection keyboard

All handlers receive dependencies via DatabaseMiddleware injection:
  user, reminder_dao, scheduler_service, l10n, state

Module family (in the order their sub-routers are included below — this
order matters, see the comment on `router` further down):
  reminders_shared.py      — constants, module-level state, and helper
                              functions used across the other five modules
                              (and, for a few names, by bot/handlers/
                              habits.py, bot/handlers/menu.py, and
                              bot/__main__.py directly).
  reminders_wizard.py       — free-text entry → time selection → save;
                              the parse-confidence confirmation step.
  reminders_repeat.py       — the RRULE repeat builder (rrb_* callbacks),
                              plus nagging-toggle/delete on the edit
                              keyboard.
  reminders_listing.py      — task list/pagination, /find and its filters,
                              missed-recovery digest bulk actions, the
                              nag-limit prompt, recently-completed tasks.
  reminders_completion.py   — marking a reminder done (wrap-up, done_task_/
                              done_undo_/done_note_/done_skip_next_).
  reminders_snooze.py        — snooze actions, and the catch-all handler
                              for a non-text message outside any FSM flow
                              — this one MUST stay included last (see its
                              own module docstring).

This file re-exports every name another module in this codebase — or a
test driving a handler directly — imports from "bot.handlers.reminders"
by name, so nothing outside this package needs to know it's a
composition of six files rather than one.
"""

from aiogram import Router

import bot.handlers.reminders_completion as reminders_completion
import bot.handlers.reminders_listing as reminders_listing
import bot.handlers.reminders_repeat as reminders_repeat
import bot.handlers.reminders_shared as reminders_shared
import bot.handlers.reminders_snooze as reminders_snooze
import bot.handlers.reminders_wizard as reminders_wizard

# --- reminders_completion ---
from bot.handlers.reminders_completion import (  # noqa: E402
    callback_done_close,
    callback_done_note,
    callback_done_skip_next,
    callback_done_undo,
    callback_task_done,
    callback_wrapup_done,
    callback_wrapup_not_done,
    callback_wrapup_selected,
    callback_wrapup_task_label,
    state_done_note,
)

# --- reminders_listing ---
from bot.handlers.reminders_listing import (  # noqa: E402
    RECOVERY_DIGEST_LIMIT,
    _render_filtered_tasks_text,
    _show_filtered_tasks,
    callback_close_tasks,
    callback_delete_task,
    callback_edit_set_nag_limit,
    callback_noop,
    callback_recovery_done_all,
    callback_recovery_snooze_all,
    callback_show_completed,
    callback_task_settings,
    callback_tasks_filter_by_tag,
    callback_tasks_filter_overdue,
    callback_tasks_filter_recurring,
    callback_tasks_filter_today,
    callback_tasks_filter_week,
    callback_tasks_page,
    callback_tasks_tags_menu,
    callback_undo_delete,
    cmd_find,
    get_tasks_list_keyboard,
    state_nag_limit,
)

# --- reminders_repeat ---
from bot.handlers.reminders_repeat import (  # noqa: E402
    callback_edit_delete,
    callback_edit_nagging,
    callback_edit_repeat_menu,
    callback_rrb_back,
    callback_rrb_customdays_open,
    callback_rrb_daily,
    callback_rrb_end_menu,
    callback_rrb_end_none,
    callback_rrb_endcount_prompt,
    callback_rrb_enduntil_prompt,
    callback_rrb_interval_prompt,
    callback_rrb_last_weekday,
    callback_rrb_monthly_prompt,
    callback_rrb_none,
    callback_rrb_open,
    callback_rrb_toggle_weekday,
    callback_rrb_weekday_done,
    callback_rrb_weekdays,
    callback_rrb_weekend,
    callback_rrb_weekly,
    state_rrb_end_count,
    state_rrb_end_until,
    state_rrb_interval,
    state_rrb_monthday,
)

# --- reminders_shared: constants, module state, and helpers also used
# directly by bot/handlers/habits.py, bot/handlers/menu.py, bot/__main__.py,
# and several tests that drive a handler function directly. ---
from bot.handlers.reminders_shared import (  # noqa: E402
    _COMPLETED_HISTORY_LIMIT,
    _MENU_TEXTS,
    _TASKS_PAGE_SIZE,
    _UNDO_DELETE_WINDOW,
    _cleanup_stale_timers,
    _format_task_line_md2,
    _handle_parsed_result,
    _message_task_key,
    _paginate_tasks_for_list,
    _parse_rrule_parts,
    _pick_done_reply,
    _remove_keyboard_after_delay,
    _render_tasks_list_text,
    _rrule_end_label,
    _rrule_text,
    _save_and_show_edit,
    active_auto_delete_tasks,
)

# --- reminders_snooze (includes the catch-all — see its own docstring on
# why its sub-router must be included last, below) ---
from bot.handlers.reminders_snooze import (  # noqa: E402
    callback_snooze_act,
    callback_snooze_show,
    handle_non_text_message,
)

# --- reminders_wizard ---
from bot.handlers.reminders_wizard import (  # noqa: E402
    callback_edit_edit,
    callback_parse_confirm_cancel,
    callback_parse_confirm_pick_time,
    callback_parse_confirm_yes,
    callback_time_selected,
    handle_forwarded_task,
    handle_task_text,
    parser,
    state_choosing_time_text_input,
    state_confirming_parse_new_text,
)
from bot.states.reminder import ReminderWizard

# Order matters: reminders_wizard's handle_task_text (StateFilter(None),
# F.text) must not shadow anything registered after it, and
# reminders_snooze's handle_non_text_message (StateFilter(None), ~F.text)
# is a catch-all that must get every more specific handler's first refusal
# — both constraints are exactly why the original monolithic file ordered
# its sections this way, preserved here via sub-router inclusion order.
#
# aiogram's Router.include_router() permanently sets the child's
# parent_router and raises if called twice on the same child — fine for a
# module imported once, but several tests in this codebase load THIS file
# a second time via importlib.util.spec_from_file_location (a fresh
# reminders.py module object each time, e.g. to patch a dependency in
# isolation) while reminders_wizard/repeat/listing/completion/snooze stay
# NORMAL cached imports (the same Router objects every time, already
# attached from the first load). Cache the composed router on
# reminders_shared — a normally-imported, genuinely-singleton module — so
# every load after the first reuses it instead of re-attempting
# include_router on already-attached sub-routers.
if getattr(reminders_shared, "_composed_reminders_router", None) is not None:
    router = reminders_shared._composed_reminders_router
else:
    router = Router(name="reminders")
    router.include_router(reminders_wizard.router)
    router.include_router(reminders_repeat.router)
    router.include_router(reminders_listing.router)
    router.include_router(reminders_completion.router)
    router.include_router(reminders_snooze.router)
    reminders_shared._composed_reminders_router = router

__all__ = [
    "ReminderWizard",
    "RECOVERY_DIGEST_LIMIT",
    "_COMPLETED_HISTORY_LIMIT",
    "_MENU_TEXTS",
    "_TASKS_PAGE_SIZE",
    "_UNDO_DELETE_WINDOW",
    "_cleanup_stale_timers",
    "_format_task_line_md2",
    "_handle_parsed_result",
    "_message_task_key",
    "_paginate_tasks_for_list",
    "_parse_rrule_parts",
    "_pick_done_reply",
    "_remove_keyboard_after_delay",
    "_render_filtered_tasks_text",
    "_render_tasks_list_text",
    "_rrule_end_label",
    "_rrule_text",
    "_save_and_show_edit",
    "_show_filtered_tasks",
    "active_auto_delete_tasks",
    "callback_close_tasks",
    "callback_delete_task",
    "callback_done_close",
    "callback_done_note",
    "callback_done_skip_next",
    "callback_done_undo",
    "callback_edit_delete",
    "callback_edit_edit",
    "callback_edit_nagging",
    "callback_edit_repeat_menu",
    "callback_edit_set_nag_limit",
    "callback_noop",
    "callback_parse_confirm_cancel",
    "callback_parse_confirm_pick_time",
    "callback_parse_confirm_yes",
    "callback_recovery_done_all",
    "callback_recovery_snooze_all",
    "callback_rrb_back",
    "callback_rrb_customdays_open",
    "callback_rrb_daily",
    "callback_rrb_end_menu",
    "callback_rrb_endcount_prompt",
    "callback_rrb_enduntil_prompt",
    "callback_rrb_end_none",
    "callback_rrb_interval_prompt",
    "callback_rrb_last_weekday",
    "callback_rrb_monthly_prompt",
    "callback_rrb_none",
    "callback_rrb_open",
    "callback_rrb_toggle_weekday",
    "callback_rrb_weekday_done",
    "callback_rrb_weekdays",
    "callback_rrb_weekend",
    "callback_rrb_weekly",
    "callback_show_completed",
    "callback_snooze_act",
    "callback_snooze_show",
    "callback_task_done",
    "callback_task_settings",
    "callback_tasks_filter_by_tag",
    "callback_tasks_filter_overdue",
    "callback_tasks_filter_recurring",
    "callback_tasks_filter_today",
    "callback_tasks_filter_week",
    "callback_tasks_page",
    "callback_tasks_tags_menu",
    "callback_time_selected",
    "callback_undo_delete",
    "callback_wrapup_done",
    "callback_wrapup_not_done",
    "callback_wrapup_selected",
    "callback_wrapup_task_label",
    "cmd_find",
    "get_tasks_list_keyboard",
    "handle_forwarded_task",
    "handle_non_text_message",
    "handle_task_text",
    "parser",
    "reminders_shared",
    "router",
    "state_choosing_time_text_input",
    "state_confirming_parse_new_text",
    "state_done_note",
    "state_nag_limit",
    "state_rrb_end_count",
    "state_rrb_end_until",
    "state_rrb_interval",
    "state_rrb_monthday",
]
