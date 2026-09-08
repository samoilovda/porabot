"""3.2: EMA-based habit score — additive alongside the streak, decays
gradually instead of zeroing out on the first missed cycle."""

from types import SimpleNamespace

from bot.services.habit_reports import compute_habit_score


def _event(outcome: str) -> SimpleNamespace:
    return SimpleNamespace(outcome=outcome)


def test_empty_history_scores_zero() -> None:
    assert compute_habit_score([]) == 0


def test_score_rises_with_consecutive_done_events() -> None:
    events = [_event("done")] * 10
    score = compute_habit_score(events)
    assert score > 80  # converges toward 100 but never instantly hits it


def test_one_miss_after_a_long_streak_does_not_zero_the_score() -> None:
    """The whole point of 3.2: unlike habit_streak_current, a single missed
    cycle must not wipe the score out."""
    events = [_event("done")] * 10 + [_event("not_today")]
    score = compute_habit_score(events)
    assert score > 50


def test_score_decays_toward_zero_with_sustained_misses() -> None:
    events = [_event("done")] * 5 + [_event("missed")] * 10
    score = compute_habit_score(events)
    assert score < 20


def test_score_stays_within_bounds() -> None:
    events = [_event("done")] * 3 + [_event("not_today")] * 3
    score = compute_habit_score(events)
    assert 0 <= score <= 100
