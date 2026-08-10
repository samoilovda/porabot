from datetime import datetime

from bot.lexicon import get_l10n
from bot.services.daily_briefs import (
    _BRIEF_ITEMS_LIMIT,
    _build_evening_text,
    _build_morning_text,
    _limit_items,
    _preview_line,
)


class _FakeTask:
    def __init__(self, i: int):
        self.execution_time = datetime(2026, 5, 1, 9, 0, 0)
        self.reminder_text = f"task {i}"


def test_limit_items_caps_at_brief_items_limit_and_reports_hidden_count() -> None:
    """Regression (REWORK_PLAN_3 1.4): an unbounded task list used to exceed
    Telegram's 4096-char message limit and the ~100-button keyboard limit —
    _send_safe's TelegramBadRequest retry (plain text, same length) fails
    identically, and the brief silently vanishes for the rest of the day.
    """
    tasks = [_FakeTask(i) for i in range(60)]

    shown, hidden = _limit_items(tasks)

    assert len(shown) == _BRIEF_ITEMS_LIMIT == 20
    assert hidden == 40


def test_limit_items_reports_zero_hidden_when_under_limit() -> None:
    tasks = [_FakeTask(i) for i in range(5)]

    shown, hidden = _limit_items(tasks)

    assert len(shown) == 5
    assert hidden == 0


def test_morning_text_shows_capped_tasks_plus_more_line() -> None:
    tasks = [_FakeTask(i) for i in range(60)]
    shown, hidden = _limit_items(tasks)
    user = type("U", (), {"timezone": "UTC", "show_utc_offset": False})()
    l10n = get_l10n("en")

    text = _build_morning_text(shown, hidden, user, l10n)

    assert text.count("task ") == 20
    assert "and 40 more" in text


def test_evening_text_caps_pending_and_completed_independently() -> None:
    completed = [_FakeTask(i) for i in range(3)]
    pending = [_FakeTask(i) for i in range(60)]
    shown_completed, hidden_completed = _limit_items(completed)
    shown_pending, hidden_pending = _limit_items(pending)
    user = type("U", (), {"timezone": "UTC", "show_utc_offset": False})()
    l10n = get_l10n("en")

    text = _build_evening_text(
        shown_completed, hidden_completed, shown_pending, hidden_pending, len(completed), len(pending), user, l10n
    )

    # Completed: all 3 shown, no "more" line for it.
    assert text.count("~task ") == 3
    # Pending: capped at 20, "and 40 more" appears.
    assert text.count("❌ task ") == 20
    assert "and 40 more" in text
    # Header counts reflect the TRUE totals, not the truncated shown counts.
    assert "Remaining/Missed: 60" in text
    assert "Done: 3" in text


def test_preview_line_truncates_long_reminder_text() -> None:
    """A handful of near-max-length reminder texts (up to 3000 chars each,
    see ReminderDAO.MAX_TEXT_LENGTH) can blow the 4096-char message limit
    even with the item count capped — each rendered line must be bounded
    independently."""
    long_text = "x" * 3000

    preview = _preview_line(long_text)

    assert len(preview) == 100
    assert preview.endswith("…")


def test_preview_line_leaves_short_text_untouched() -> None:
    assert _preview_line("buy milk") == "buy milk"
