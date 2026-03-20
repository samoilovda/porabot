"""Lexicon module for i18n support."""

from typing import Any, Optional

from bot.lexicon.en import EN
from bot.lexicon.ru import RU

# Default language string if None is provided
DEFAULT_LANG = "ru"

_LEXICONS: dict[str, dict[str, Any]] = {
    "ru": RU,
    "en": EN,
}

def get_l10n(language_code: Optional[str]) -> dict[str, Any]:
    """Retrieve the translation dictionary for a given language code."""
    if not language_code:
        return _LEXICONS[DEFAULT_LANG]
    return _LEXICONS.get(language_code, _LEXICONS[DEFAULT_LANG])
