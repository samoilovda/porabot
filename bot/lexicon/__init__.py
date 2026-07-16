"""Lexicon module for i18n support."""

from typing import Any, Optional

from bot.lexicon.en import EN
from bot.lexicon.ru import RU
from bot.lexicon.es import ES

# Default language string if None is provided
DEFAULT_LANG = "ru"

_LEXICONS: dict[str, dict[str, Any]] = {
    "ru": RU,
    "en": EN,
    "es": ES,
}

def get_l10n(language_code: Optional[str]) -> dict[str, Any]:
    """Retrieve the translation dictionary for a given language code."""
    if not language_code:
        return _LEXICONS[DEFAULT_LANG]
    return _LEXICONS.get(language_code, _LEXICONS[DEFAULT_LANG])


# Main-menu button texts, keyed by lexicon key, one frozenset per button
# holding that button's label in every supported language. Building these
# from _LEXICONS instead of hardcoding literals means a wording/emoji change
# to a btn_* lexicon entry (or a new language added to _LEXICONS) stays in
# sync everywhere these are used to recognize a menu-button tap.
_MENU_BUTTON_KEYS = ("btn_new_task", "btn_my_tasks", "btn_settings", "btn_habits")

MENU_BUTTON_TEXTS_BY_KEY: dict[str, frozenset[str]] = {
    key: frozenset(lex[key] for lex in _LEXICONS.values()) for key in _MENU_BUTTON_KEYS
}

ALL_MENU_BUTTON_TEXTS: frozenset[str] = frozenset(
    text for texts in MENU_BUTTON_TEXTS_BY_KEY.values() for text in texts
)
