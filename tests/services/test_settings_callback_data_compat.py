"""Step 9 of the 2026-09-26 audit remediation: bot/handlers/settings.py was
split into a package (locale_time.py / notifications.py / data.py, all
composed under __init__.py's `router`). Every callback_data string that
existed before the split must still resolve to a handler somewhere in
that composed router tree — an already-sent message with an old inline
keyboard in some user's chat must keep working.
"""

from types import SimpleNamespace

from bot.handlers.settings import router


def _all_routers(r):
    yield r
    for sub in r.sub_routers:
        yield from _all_routers(sub)


def _callback_data_is_handled(data: str) -> bool:
    """Whether some callback_query handler anywhere in the composed
    settings router tree would fire for this callback_data — walks every
    sub-router (aiogram doesn't expose a public "would this dispatch"
    check, so this evaluates each handler's own filters directly, the same
    way aiogram's dispatcher does internally)."""
    fake_callback = SimpleNamespace(data=data)
    for r in _all_routers(router):
        observer = r.observers.get("callback_query")
        if not observer:
            continue
        for handler in observer.handlers:
            try:
                if all(filt.callback(fake_callback) for filt in handler.filters):
                    return True
            except Exception:
                continue
    return False


_PRE_SPLIT_CALLBACK_DATA = [
    # __init__.py (top-level)
    "settings_back",
    # locale_time.py
    "settings_toggle_utc",
    "settings_change_tz",
    "settings_change_lang",
    "set_tz_Europe/Moscow",
    "set_tz_manual",
    "tzmig_all",
    "tzmig_none",
    "tzmig_pick",
    "tzmig_toggle_42",
    "tzmig_back",
    "tzmig_apply",
    # notifications.py
    "settings_quiet_setup",
    "quiet_toggle",
    "quiet_weekend_toggle",
    "quiet_habits_exempt_toggle",
    "quiet_edit_start",
    "quiet_edit_end",
    "quiet_edit_weekend_start",
    "quiet_edit_weekend_end",
    "settings_briefs_setup",
    "briefs_toggle",
    "briefs_edit_morning",
    "briefs_edit_evening",
    "settings_habit_reports_setup",
    "habit_reports_toggle",
    "habit_report_edit_day",
    "habit_report_day_3",
    "habit_report_edit_time",
    "settings_missed_recovery_setup",
    "missed_recovery_toggle",
    "missed_recovery_edit_time",
    # data.py
    "settings_export_data",
    "settings_ics_feed",
    "settings_ics_feed_regenerate",
    "settings_clear_all",
    "settings_clear_all_confirm",
]


def test_every_pre_split_callback_data_still_has_a_handler() -> None:
    unhandled = [cb for cb in _PRE_SPLIT_CALLBACK_DATA if not _callback_data_is_handled(cb)]
    assert unhandled == []


def test_an_unrelated_callback_data_is_not_handled() -> None:
    """Sanity check on the test helper itself — proves it isn't just
    returning True unconditionally."""
    assert not _callback_data_is_handled("totally_unrelated_callback")


def test_settings_router_composes_all_three_group_submodules() -> None:
    sub_router_names = {r.name for r in router.sub_routers}
    assert sub_router_names == {"settings_locale_time", "settings_notifications", "settings_data"}
