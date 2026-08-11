"""3.5: settings handlers for the weekend quiet-hours window and the
habits-exempt flag."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.settings import (
    callback_quiet_habits_exempt_toggle,
    callback_quiet_weekend_toggle,
)


def _user(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        quiet_hours_enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        quiet_hours_weekend_enabled=False,
        quiet_hours_weekend_start="23:00",
        quiet_hours_weekend_end="10:00",
        quiet_hours_habits_exempt=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_weekend_toggle_flips_and_persists() -> None:
    user = _user()
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    message = SimpleNamespace(edit_reply_markup=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())

    await callback_quiet_weekend_toggle(callback, user, user_dao, {"btn_quiet_weekend_on": "on", "btn_quiet_weekend_off": "off", "btn_quiet_on": "on", "btn_quiet_off": "off", "btn_quiet_start": "{time}", "btn_quiet_end": "{time}", "btn_back_settings": "back"}, state)

    assert user.quiet_hours_weekend_enabled is True
    user_dao.update_settings.assert_awaited_once_with(1, quiet_hours_weekend_enabled=True)
    message.edit_reply_markup.assert_awaited_once()


async def test_habits_exempt_toggle_flips_and_persists() -> None:
    user = _user()
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    message = SimpleNamespace(edit_reply_markup=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())

    l10n = {
        "btn_quiet_habits_exempt_on": "on", "btn_quiet_habits_exempt_off": "off",
        "btn_quiet_on": "on", "btn_quiet_off": "off",
        "btn_quiet_start": "{time}", "btn_quiet_end": "{time}", "btn_back_settings": "back",
    }
    await callback_quiet_habits_exempt_toggle(callback, user, user_dao, l10n, state)

    assert user.quiet_hours_habits_exempt is True
    user_dao.update_settings.assert_awaited_once_with(1, quiet_hours_habits_exempt=True)
    message.edit_reply_markup.assert_awaited_once()
