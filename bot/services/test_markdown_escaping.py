from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from bot.services.daily_briefs import _build_morning_text, _send_safe
from bot.utils.markdown import escape_markdown, strip_markdown_escapes


def test_escape_markdown_escapes_legacy_special_chars() -> None:
    raw = "снять *деньги_ [срочно"
    escaped = escape_markdown(raw)

    assert escaped == "снять \\*деньги\\_ \\[срочно"


def test_morning_brief_survives_task_text_with_unbalanced_markdown_chars() -> None:
    tasks = [
        SimpleNamespace(
            execution_time=__import__("datetime").datetime(2026, 5, 1, 9, 0, 0),
            reminder_text="снять *деньги_ [срочно",
        )
    ]
    user = SimpleNamespace(timezone="UTC", show_utc_offset=False)

    text = _build_morning_text(tasks, user, {})

    assert "\\*деньги\\_" in text
    assert "\\[срочно" in text


async def test_send_safe_falls_back_to_plain_text_on_bad_request() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    bot.send_message.side_effect = [
        TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities"),
        None,
    ]

    await _send_safe(bot, 123, "some *broken markdown")

    assert bot.send_message.await_count == 2
    first_call, second_call = bot.send_message.await_args_list
    assert first_call.kwargs["parse_mode"] == "Markdown"
    assert second_call.kwargs["parse_mode"] is None


def test_strip_markdown_escapes_reverses_escape_markdown() -> None:
    raw = "снять *деньги_ [срочно"
    escaped = escape_markdown(raw)

    assert strip_markdown_escapes(escaped) == raw


async def test_send_safe_fallback_shows_original_text_not_escaped_backslashes() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    bot.send_message.side_effect = [
        TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities"),
        None,
    ]
    escaped_text = escape_markdown("buy milk_eggs *now*")

    await _send_safe(bot, 123, escaped_text)

    _, second_call = bot.send_message.await_args_list
    assert second_call.kwargs["text"] == "buy milk_eggs *now*"
    assert "\\" not in second_call.kwargs["text"]
