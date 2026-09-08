"""4.6: the "Open progress view" web_app button only appears once
MINI_APP_URL is configured — see bot/config.py's docstring on why it's
empty by default (no public HTTPS URL to point it at yet)."""

from bot.keyboards import inline as inline_module
from bot.lexicon.ru import RU


def test_mini_app_button_absent_when_url_unset(monkeypatch) -> None:
    monkeypatch.setattr(inline_module.config, "MINI_APP_URL", "")
    markup = inline_module.get_settings_keyboard(RU, show_utc_offset=False)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert RU["btn_open_mini_app"] not in texts


def test_mini_app_button_present_when_url_set(monkeypatch) -> None:
    monkeypatch.setattr(inline_module.config, "MINI_APP_URL", "https://example.com/miniapp")
    markup = inline_module.get_settings_keyboard(RU, show_utc_offset=False)
    buttons = [b for row in markup.inline_keyboard for b in row]
    mini_app_buttons = [b for b in buttons if b.text == RU["btn_open_mini_app"]]
    assert len(mini_app_buttons) == 1
    assert mini_app_buttons[0].web_app.url == "https://example.com/miniapp"
