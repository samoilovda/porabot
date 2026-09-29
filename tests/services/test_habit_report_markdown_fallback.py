"""docs/audits/2026-09-22-audit.md#a-21 — habit_reports._send_safe used to
give up outright on a Markdown parse failure, unlike its siblings in
daily_briefs.py/missed_recovery.py, which retry once in plain text. A
single stray unescaped character anywhere in a user-controlled habit name
silently dropped the ENTIRE weekly/monthly report for that user, with
nothing else ever retrying it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from bot.services.habit_reports import _send_safe


async def test_bad_request_falls_back_to_plain_text() -> None:
    calls = []

    async def _send_message(*, chat_id, text, parse_mode=None, disable_notification=False):
        calls.append((text, parse_mode))
        if parse_mode == "Markdown":
            raise TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities")
        return SimpleNamespace()

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=_send_message))

    delivered = await _send_safe(bot, 1, "Habit *unbalanced")

    assert delivered is True
    assert len(calls) == 2
    assert calls[0][1] == "Markdown"
    assert calls[1][1] is None


async def test_forbidden_error_is_final_not_retried() -> None:
    from aiogram.exceptions import TelegramForbiddenError

    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=TelegramForbiddenError(method=SimpleNamespace(), message="blocked"))
    )

    delivered = await _send_safe(bot, 1, "Weekly report")

    assert delivered is True
    bot.send_message.assert_awaited_once()
