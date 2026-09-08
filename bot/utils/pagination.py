"""1.5: shared "cap what's rendered, say how much was cut" helpers.

Telegram caps messages at 4096 chars and inline keyboards at well under
100 buttons. bot/services/daily_briefs.py already solved this for the
morning/evening brief with its own `_limit_items`/`_preview_line` pair;
bot/handlers/reminders.py's task list solved it with `_TASKS_PAGE_SIZE`
pagination. Neither was reused for the screens this module now backs
(completed-tasks history, the habits list, weekly/monthly habit reports),
which had no bound at all — a long enough history or habit list could
exceed both limits and the `edit_text` call would raise, dropping the
screen for the user with no fallback.
"""

from typing import Sequence, TypeVar

T = TypeVar("T")


def limit_items(items: Sequence[T], limit: int) -> tuple[list[T], int]:
    """Return (shown, hidden_count) — the first *limit* items and how many
    were cut. *limit* must be positive; callers pick it per screen based on
    how many lines/buttons that screen can afford."""
    items = list(items)
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def preview_line(text: str, limit: int = 100) -> str:
    """Truncate *text* to *limit* chars with an ellipsis, if needed — caps a
    single long reminder/habit text from blowing the message-length budget
    even when the item COUNT is already bounded by `limit_items`."""
    return text if len(text) <= limit else text[: limit - 1] + "…"
