"""Regression test for 1.5: _build_report_text must not exceed Telegram's
4096-char message limit when a user has many habits with report activity
(ReminderDAO.MAX_ACTIVE_HABITS allows up to 50) — and the "Итого" total
must still reflect ALL rows, not just the ones actually rendered.
"""

from datetime import date

from bot.lexicon import get_l10n
from bot.services.habit_reports import _REPORT_ROWS_LIMIT, _build_report_text


def _row(reminder_id: int, text: str, done: int = 5, not_today: int = 2) -> dict:
    total = done + not_today
    return {
        "reminder_id": reminder_id,
        "habit_text": text,
        "done": done,
        "not_today": not_today,
        "total": total,
        "rate": round(done / total * 100),
    }


def test_report_caps_rendered_rows_and_notes_the_rest() -> None:
    l10n = get_l10n("en")
    rows = [_row(i, f"Habit number {i}") for i in range(1, 41)]  # over the 30-row cap

    text = _build_report_text(
        title="Weekly report {start}-{end}",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
    )

    assert len(text) < 4096
    assert text.count("Habit number") == _REPORT_ROWS_LIMIT
    assert l10n.get("brief_items_more", "…and {count} more").format(
        count=len(rows) - _REPORT_ROWS_LIMIT
    ) in text


def test_report_total_counts_every_row_even_when_not_all_are_shown() -> None:
    l10n = get_l10n("en")
    rows = [_row(i, f"Habit {i}", done=1, not_today=0) for i in range(1, 41)]

    text = _build_report_text(
        title="Weekly report",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
    )

    # 40 rows * 1 "done" each == 40, even though only _REPORT_ROWS_LIMIT
    # rows are individually listed.
    expected_total = l10n.get("habit_report_total", "**Итого:** ✅ {done} · ❌ {not_done} ({rate}%)").format(
        done=40, not_done=0, rate=100
    )
    assert expected_total in text


def test_report_truncates_a_very_long_habit_text() -> None:
    l10n = get_l10n("en")
    long_text = "z" * 3000
    rows = [_row(1, long_text)]

    text = _build_report_text(
        title="Weekly report",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
    )

    assert long_text not in text
    assert len(text) < 4096
