"""docs/audits/2026-09-22-audit.md#a-08 — "🚫 Not applicable" on a plain
recurring reminder's fresh notification used to route to done_skip_next_,
the same callback the post-Done follow-up keyboard's "Skip next" button
uses. By the time either button is tapped, _execute_reminder has already
advanced execution_time to the series' correct next occurrence — so
done_skip_next_'s "skip the upcoming one" semantics, correct for the
follow-up keyboard, silently dropped that already-correct next occurrence
here instead of dismissing the cycle that had just fired.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup

from bot.handlers.reminders_completion import callback_not_relevant
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon.ru import RU as RU_LEXICON


def _find_button(markup: InlineKeyboardMarkup, callback_data: str):
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data == callback_data:
                return button
    return None


async def test_not_relevant_leaves_the_already_advanced_next_occurrence_untouched() -> None:
    # Simulates the moment right after a daily 09:00 reminder fired and
    # _execute_reminder already advanced it to tomorrow 09:00 (the correct
    # next occurrence) before the user ever taps anything.
    correct_next_occurrence = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=20)
    reminder = SimpleNamespace(
        id=42,
        user_id=1,
        execution_time=correct_next_occurrence,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        rrule_dtstart=correct_next_occurrence - timedelta(days=30),
        completed_for_execution_time=None,
        last_nag_chat_id=123,
        last_nag_message_id=456,
        snooze_count=2,
    )

    # The keyboard shown on that fresh notification routes "Not done" to
    # not_relevant_, not done_skip_next_.
    markup = get_task_done_keyboard(
        reminder_id=reminder.id, l10n=RU_LEXICON, show_not_done=True, is_recurring=True
    )
    button = _find_button(markup, f"not_relevant_{reminder.id}")
    assert button is not None

    async def _mark_not_today(reminder_id: int) -> None:
        # Same tracking-reset logic as ReminderDAO.mark_habit_not_today —
        # inlined here so this test doesn't need a real DB session, while
        # still exercising callback_not_relevant's real production code.
        assert reminder_id == reminder.id
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None
        reminder.snooze_count = 0

    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        mark_habit_not_today=AsyncMock(side_effect=_mark_not_today),
        session=session,
    )
    scheduler_service = SimpleNamespace(remove_nagging_job=lambda rid: None)
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = SimpleNamespace(
        data=button.callback_data,
        message=SimpleNamespace(text="Standup", edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_not_relevant(callback, reminder_dao, scheduler_service, user, RU_LEXICON)

    # The already-correct next occurrence must survive untouched — this is
    # the exact value done_skip_next_ would have advanced PAST.
    assert reminder.execution_time == correct_next_occurrence
    # The nag chain for the dismissed cycle is closed.
    assert reminder.last_nag_chat_id is None
    assert reminder.last_nag_message_id is None
    assert reminder.snooze_count == 0
    # No explicit commit here — same as the sibling habits.cb_not_today
    # handler, this relies on DatabaseMiddleware's implicit commit once the
    # handler returns successfully.
    session.commit.assert_not_awaited()
