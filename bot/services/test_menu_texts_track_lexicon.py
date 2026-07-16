"""Menu-button router filters must be derived from the lexicon, not
hardcoded literals, so a wording/emoji change to a btn_* lexicon entry (or a
new language added to bot.lexicon._LEXICONS) can't silently desync from the
filters that recognize a menu-button tap.
"""

import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL-ABCDEFGHIJKLMNOPQRS")

import bot.handlers.menu as menu
import bot.handlers.reminders as reminders
from bot.lexicon import ALL_MENU_BUTTON_TEXTS, _LEXICONS


def _handler_filter_matches(handler_callback, text: str) -> bool:
    for h in menu.router.message.handlers:
        if h.callback is handler_callback:
            return bool(h.filters[0].magic.resolve(SimpleNamespace(text=text)))
    raise AssertionError(f"handler {handler_callback} not registered on menu.router")


def test_menu_router_filters_match_every_language_for_their_button() -> None:
    handler_by_key = {
        "btn_new_task": menu.btn_new_task,
        "btn_my_tasks": menu.btn_my_tasks,
        "btn_settings": menu.btn_settings,
        "btn_habits": menu.btn_habits,
    }
    for key, handler in handler_by_key.items():
        for lex in _LEXICONS.values():
            assert _handler_filter_matches(handler, lex[key])
    # A button's filter must not accidentally match a different button's text.
    assert not _handler_filter_matches(menu.btn_new_task, _LEXICONS["en"]["btn_settings"])


def test_reminders_menu_texts_is_the_shared_lexicon_set() -> None:
    assert reminders._MENU_TEXTS is ALL_MENU_BUTTON_TEXTS


def test_every_language_button_label_is_covered_by_the_combined_set() -> None:
    for lex in _LEXICONS.values():
        for key in ("btn_new_task", "btn_my_tasks", "btn_settings", "btn_habits"):
            assert lex[key] in ALL_MENU_BUTTON_TEXTS
