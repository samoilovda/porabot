"""A plain (non-habit) task's fired-reminder keyboard now offers a "Not
done" action alongside Done/snooze, for a task that loses relevance while
it's being nagged about. A one-off task's button routes to del_task_
(soft-delete with Undo, same as the task list's Delete). A recurring
plain task's button routes to not_relevant_ (reminders_completion.
callback_not_relevant) — NOT done_skip_next_ (docs/audits/
2026-09-22-audit.md#a-08): by the time this button is tapped, the
scheduler has already advanced execution_time to the correct next
occurrence, so done_skip_next_'s "skip the upcoming one" semantics would
silently drop that next occurrence instead of dismissing the cycle that
just fired. Habit-like/fluid reminders keep their existing "Not today"
instead and must never show both.
"""

from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon.ru import RU as RU_LEXICON


def _callback_datas(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_one_off_task_gets_not_done_routed_to_soft_delete() -> None:
    markup = get_task_done_keyboard(
        reminder_id=7,
        l10n=RU_LEXICON,
        show_not_today=False,
        show_not_done=True,
        is_recurring=False,
    )

    datas = _callback_datas(markup)
    assert "del_task_7" in datas
    assert "done_skip_next_7" not in datas
    assert "not_today_7" not in datas


def test_recurring_plain_task_gets_not_done_routed_to_not_relevant() -> None:
    markup = get_task_done_keyboard(
        reminder_id=8,
        l10n=RU_LEXICON,
        show_not_today=False,
        show_not_done=True,
        is_recurring=True,
    )

    datas = _callback_datas(markup)
    assert "not_relevant_8" in datas
    assert "del_task_8" not in datas
    assert "done_skip_next_8" not in datas


def test_habit_keeps_not_today_and_never_shows_not_done_too() -> None:
    markup = get_task_done_keyboard(
        reminder_id=9,
        l10n=RU_LEXICON,
        show_not_today=True,
        show_not_done=True,  # a caller bug shouldn't be able to show both
        is_recurring=True,
    )

    datas = _callback_datas(markup)
    assert "not_today_9" in datas
    assert "del_task_9" not in datas
    assert "not_relevant_9" not in datas


def test_neither_flag_shows_no_secondary_row() -> None:
    markup = get_task_done_keyboard(reminder_id=10, l10n=RU_LEXICON)

    datas = _callback_datas(markup)
    assert not any(d.startswith(("not_today_", "del_task_", "not_relevant_")) for d in datas)
