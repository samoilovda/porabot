"""Domain validation errors raised by DAOs (B-13, 2026-11-04 audit).

They subclass ValueError so existing ``except ValueError`` call sites keep
working, but carry structured fields so handlers can render a localized
message (see ``user_message``) instead of showing the English exception
text to the user.
"""

from typing import Any


class ReminderValidationError(ValueError):
    """Base class: ``lexicon_key`` + ``params`` describe the user message."""

    lexicon_key = "generic_error"

    def __init__(self, message: str, **params: Any) -> None:
        super().__init__(message)
        self.params = params


class ReminderTextTooLongError(ReminderValidationError):
    lexicon_key = "text_too_long"


class ActiveRemindersLimitError(ReminderValidationError):
    lexicon_key = "limit_reminders_reached"


class ActiveHabitsLimitError(ReminderValidationError):
    lexicon_key = "limit_habits_reached"


def user_message(error: Exception, l10n: dict[str, Any]) -> str:
    """Localized text for *error*; a generic error for anything unknown —
    never the raw (English, developer-facing) exception text."""
    if isinstance(error, ReminderValidationError):
        try:
            return l10n[error.lexicon_key].format(**error.params)
        except (KeyError, IndexError):
            pass
    return l10n["generic_error"]
