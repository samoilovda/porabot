"""Regression tests: after a settings flow that only edited a message or
sent inline-keyboard-only replies, the persistent bottom menu (reply
keyboard) must still get re-attached to at least one message. If the user
collapsed it (e.g. to type a timezone offset or a time value — exactly what
these flows ask for), nothing else in the flow would ever bring it back.

Covers the timezone-change (manual-entry, no-habits-to-migrate) path and
the four settings screens that accept typed HH:MM input.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import ReplyKeyboardMarkup

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_handler(fn_name: str):
    module_path = ROOT / "bot/handlers/settings.py"
    spec = importlib.util.spec_from_file_location(f"test_{fn_name}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, fn_name)


def _reply_markup_calls(mock: AsyncMock) -> list:
    """reply_markup values across every call, whether passed positionally
    or by keyword."""
    values = []
    for call in mock.await_args_list:
        if "reply_markup" in call.kwargs:
            values.append(call.kwargs["reply_markup"])
        elif len(call.args) > 1:
            values.append(call.args[1])
    return values


async def test_manual_timezone_entry_with_no_habits_resends_main_menu() -> None:
    state_set_manual_timezone = _load_handler("state_set_manual_timezone")
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1, timezone="Europe/Moscow")
    user_dao = SimpleNamespace(update_timezone=AsyncMock())
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=[]))
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={}),
        clear=AsyncMock(),
        update_data=AsyncMock(),
    )
    message = SimpleNamespace(text="+3", from_user=SimpleNamespace(first_name="Bob"), answer=AsyncMock())

    await state_set_manual_timezone(
        message=message, state=state, user=user, user_dao=user_dao, l10n=l10n, reminder_dao=reminder_dao
    )

    markups = _reply_markup_calls(message.answer)
    assert any(isinstance(m, ReplyKeyboardMarkup) for m in markups), (
        "no message carried the persistent bottom menu back after a no-habits timezone change"
    )


async def test_quiet_hours_time_input_keeps_main_menu_attached() -> None:
    state_set_quiet_time = _load_handler("state_set_quiet_time")
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1, timezone="UTC", quiet_hours_start="23:00")
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"quiet_target": "start"}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(text="22:30", answer=AsyncMock())

    await state_set_quiet_time(message=message, state=state, user=user, user_dao=user_dao, l10n=l10n)

    markups = _reply_markup_calls(message.answer)
    assert any(isinstance(m, ReplyKeyboardMarkup) for m in markups)


async def test_briefs_time_input_resends_main_menu() -> None:
    state_briefs_set_time = _load_handler("state_briefs_set_time")
    l10n = get_l10n("en")

    user = SimpleNamespace(
        id=1, timezone="UTC", briefs_enabled=True, morning_brief_time="09:00", evening_brief_time="23:00"
    )
    user_dao = SimpleNamespace(update_briefs_settings=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"brief_target": "morning"}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(text="08:15", answer=AsyncMock())

    await state_briefs_set_time(message=message, state=state, user=user, user_dao=user_dao, l10n=l10n)

    markups = _reply_markup_calls(message.answer)
    assert any(isinstance(m, ReplyKeyboardMarkup) for m in markups)


async def test_habit_report_time_input_keeps_main_menu_attached() -> None:
    state_habit_report_set_time = _load_handler("state_habit_report_set_time")
    l10n = get_l10n("en")

    user = SimpleNamespace(
        id=1, timezone="UTC", habit_reports_enabled=True, habit_report_weekday=6, habit_report_time="23:50"
    )
    user_dao = SimpleNamespace(update_habit_report_settings=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(text="21:00", answer=AsyncMock())

    await state_habit_report_set_time(message=message, state=state, user=user, user_dao=user_dao, l10n=l10n)

    markups = _reply_markup_calls(message.answer)
    assert any(isinstance(m, ReplyKeyboardMarkup) for m in markups)


async def test_missed_recovery_time_input_keeps_main_menu_attached() -> None:
    state_missed_recovery_set_time = _load_handler("state_missed_recovery_set_time")
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1, timezone="UTC", missed_recovery_enabled=True, missed_recovery_time="10:00")
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(text="11:30", answer=AsyncMock())

    await state_missed_recovery_set_time(message=message, state=state, user=user, user_dao=user_dao, l10n=l10n)

    markups = _reply_markup_calls(message.answer)
    assert any(isinstance(m, ReplyKeyboardMarkup) for m in markups)
