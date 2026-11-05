"""Lexicon module for i18n support."""

from typing import Any, Optional

from bot.lexicon.en import EN
from bot.lexicon.es import ES
from bot.lexicon.pt import PT
from bot.lexicon.ru import RU

# Default language string if None is provided
DEFAULT_LANG = "ru"

_LEXICONS: dict[str, dict[str, Any]] = {
    "ru": RU,
    "en": EN,
    "es": ES,
    "pt": PT,
}

def get_l10n(language_code: Optional[str]) -> dict[str, Any]:
    """Retrieve the translation dictionary for a given language code."""
    if not language_code:
        return _LEXICONS[DEFAULT_LANG]
    if not isinstance(language_code, str):
        return _LEXICONS[DEFAULT_LANG]
    if language_code in _LEXICONS:
        return _LEXICONS[language_code]
    # Telegram reports regional variants ("pt-br", "es-419", "en_US"); the
    # primary subtag is what picks the lexicon.
    primary = language_code.lower().replace("_", "-").split("-")[0]
    return _LEXICONS.get(primary, _LEXICONS[DEFAULT_LANG])


def plural(l10n: dict[str, Any], key: str, count: int) -> str:
    """"{count} <word>" with the right plural form (U-07, 2026-11-04 audit).

    *key* names a list of forms in the lexicon: three for Russian
    (one/few/many: 1 задача, 2 задачи, 5 задач), two for en/es/pt
    (one/other). Avoids "1 задач" / "1 tasks"."""
    forms = l10n[key]
    n = abs(int(count))
    if len(forms) == 3:
        if n % 10 == 1 and n % 100 != 11:
            word = forms[0]
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            word = forms[1]
        else:
            word = forms[2]
    else:
        word = forms[0] if n == 1 else forms[1]
    return f"{count} {word}"


# A-23: the set of language codes this bot actually has a lexicon for —
# callback_set_lang (bot/handlers/commands.py) validates a client-supplied
# set_lang_<code> callback against this instead of persisting any string
# Telegram happens to hand back. Derived from _LEXICONS' own keys so a
# future language added there is automatically accepted here too.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(_LEXICONS.keys())


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
