"""docs/audits/2026-09-22-audit.md#a-22 — roughly a dozen callback handlers
parsed their trailing id with a bare `int(callback.data.split(prefix)[1])`,
no guard at all. callback_data is client-controlled — Telegram doesn't
cryptographically bind it to the keyboard actually shown — so a forged or
stale value (an old message, a changed id scheme) raised an uncaught
IndexError/ValueError that propagated to bot/__main__.py's
handle_dispatcher_error, showing a generic "something went wrong" alert
instead of the specific, already-existing "invalid_action" one every
guarded call site already used.

Each handler below is driven with callback.data holding garbage after its
prefix — before the fix, this raised inside the handler (pytest would
report an error, not a clean assertion failure); after the fix, it answers
invalid_action and never touches the DAO.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.habits import cb_del_habit
from bot.handlers.reminders_completion import callback_done_note, callback_done_skip_next
from bot.handlers.reminders_listing import (
    callback_delete_task,
    callback_edit_set_nag_limit,
    callback_task_settings,
    callback_undo_delete,
)
from bot.handlers.reminders_repeat import callback_edit_nagging, callback_rrb_daily
from bot.handlers.reminders_wizard import callback_edit_edit
from bot.lexicon import get_l10n

L10N = get_l10n("en")


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=1),
            message_id=1,
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
    )


@pytest.mark.parametrize(
    "handler,bad_data,extra_kwargs",
    [
        (callback_task_settings, "task_settings_not-a-number", {}),
        (callback_delete_task, "del_task_", {"scheduler_service": SimpleNamespace()}),
        (callback_undo_delete, "undo_del_abc", {"scheduler_service": SimpleNamespace()}),
        (callback_edit_set_nag_limit, "edit_set_nag_limit_xyz", {"state": SimpleNamespace()}),
        (callback_edit_edit, "edit_edit_", {"state": SimpleNamespace()}),
        (callback_done_note, "done_note_nope", {"state": SimpleNamespace()}),
        (
            callback_done_skip_next,
            "done_skip_next_",
            {"scheduler_service": SimpleNamespace()},
        ),
        (
            callback_edit_nagging,
            "edit_toggle_nagging_",
            {"scheduler_service": SimpleNamespace()},
        ),
        (callback_rrb_daily, "rrb_daily_garbage", {"scheduler_service": SimpleNamespace()}),
        (cb_del_habit, "del_habit_notanumber", {"scheduler_service": SimpleNamespace()}),
    ],
)
async def test_malformed_callback_id_is_rejected_not_raised(handler, bad_data, extra_kwargs) -> None:
    reminder_dao = SimpleNamespace(get_owned=AsyncMock(side_effect=AssertionError("must not query the DAO")))
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = _callback(bad_data)

    await handler(callback=callback, reminder_dao=reminder_dao, user=user, l10n=L10N, **extra_kwargs)

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.args[0] == L10N["invalid_action"]
    assert callback.answer.await_args.kwargs.get("show_alert") is True
