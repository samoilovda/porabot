"""Step 7 of the 2026-09-26 audit remediation: the EMA habit score
("Устойчивость") must be explained once and shown on its own line,
separate from the plain done/total completion fraction — not crammed onto
the same line looking like another way of expressing that same fraction.
"""

from datetime import date

from bot.lexicon import get_l10n
from bot.services.habit_reports import _build_report_text


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


def test_score_line_is_separate_from_the_completion_fraction_line() -> None:
    l10n = get_l10n("en")
    rows = [_row(1, "Workout", done=5, not_today=2)]

    text = _build_report_text(
        title="Weekly report",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
        scores_by_id={1: 62},
    )

    lines = text.splitlines()
    fraction_line = next(line for line in lines if "Workout" in line)
    score_line = next(line for line in lines if "62/100" in line)

    assert fraction_line != score_line
    assert "62" not in fraction_line
    assert "Done 5 of 7" in fraction_line


def test_score_explainer_is_shown_once_not_per_habit() -> None:
    l10n = get_l10n("en")
    rows = [_row(i, f"Habit {i}") for i in range(1, 4)]

    text = _build_report_text(
        title="Weekly report",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
        scores_by_id={1: 10, 2: 20, 3: 30},
    )

    explainer = l10n.get("habit_score_explainer")
    assert text.count(explainer) == 1


def test_no_score_line_when_a_habit_has_no_score_yet() -> None:
    l10n = get_l10n("en")
    rows = [_row(1, "Workout")]

    text = _build_report_text(
        title="Weekly report",
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        rows=rows,
        reminders_by_id={},
        l10n=l10n,
        scores_by_id={},
    )

    assert "/100" not in text
