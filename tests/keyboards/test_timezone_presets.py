"""U-04 (2026-11-04 audit): timezone presets follow the user's language."""

import pytest
import pytz

from bot.keyboards.inline import get_timezone_keyboard
from bot.lexicon import get_l10n


def _zones(lang):
    kb = get_timezone_keyboard(get_l10n(lang))
    return [b.callback_data.removeprefix("set_tz_") for row in kb.inline_keyboard for b in row
            if b.callback_data.startswith("set_tz_") and b.callback_data != "set_tz_manual"]


@pytest.mark.parametrize("lang, first, must_have", [
    ("ru", "Europe/Moscow", {"Europe/Minsk", "Asia/Yekaterinburg", "Asia/Novosibirsk", "Asia/Tashkent"}),
    ("es", "Europe/Madrid", {"America/Mexico_City", "America/Argentina/Buenos_Aires"}),
    ("pt", "America/Sao_Paulo", {"Europe/Lisbon"}),
    ("en", "America/New_York", {"Europe/London"}),
])
def test_presets_match_the_audience(lang, first, must_have) -> None:
    zones = _zones(lang)
    assert zones[0] == first
    assert must_have <= set(zones)
    assert "UTC" in zones


@pytest.mark.parametrize("lang", ["ru", "en", "es", "pt"])
def test_every_preset_is_a_valid_zone_and_fits_callback_data(lang) -> None:
    kb = get_timezone_keyboard(get_l10n(lang))
    for row in kb.inline_keyboard:
        assert len(row) <= 2
        for button in row:
            assert len(button.callback_data.encode()) <= 64
    for zone in _zones(lang):
        pytz.timezone(zone)
