"""docs/audits/2026-09-22-audit.md#a-17 — the "📅 Calendar feed" button used
to always show, even when WEB_SERVER_ENABLED is False (its default) —
tapping it generated a feed token and showed the user a link under
http://localhost:{WEB_SERVER_PORT}, unreachable from anywhere outside the
host it runs on. Same "don't ship a button that can never work" reasoning
already applied to MINI_APP_URL (test_settings_keyboard_miniapp.py)."""

from bot.keyboards import inline as inline_module
from bot.lexicon.ru import RU


def test_ics_feed_button_absent_when_web_server_disabled(monkeypatch) -> None:
    monkeypatch.setattr(inline_module.config, "WEB_SERVER_ENABLED", False)
    monkeypatch.setattr(inline_module.config, "PUBLIC_BASE_URL", "")
    markup = inline_module.get_settings_keyboard(RU, show_utc_offset=False)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert RU["btn_ics_feed"] not in texts


def test_ics_feed_button_absent_when_enabled_but_no_public_url(monkeypatch) -> None:
    """WEB_SERVER_ENABLED alone isn't enough — without PUBLIC_BASE_URL the
    feed link would still only ever resolve to localhost."""
    monkeypatch.setattr(inline_module.config, "WEB_SERVER_ENABLED", True)
    monkeypatch.setattr(inline_module.config, "PUBLIC_BASE_URL", "")
    markup = inline_module.get_settings_keyboard(RU, show_utc_offset=False)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert RU["btn_ics_feed"] not in texts


def test_ics_feed_button_present_when_web_server_enabled_and_public_url_set(monkeypatch) -> None:
    monkeypatch.setattr(inline_module.config, "WEB_SERVER_ENABLED", True)
    monkeypatch.setattr(inline_module.config, "PUBLIC_BASE_URL", "https://porabot.example.com")
    markup = inline_module.get_settings_keyboard(RU, show_utc_offset=False)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert RU["btn_ics_feed"] in texts
