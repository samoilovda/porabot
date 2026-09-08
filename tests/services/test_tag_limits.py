"""Regression (fix 1.3): tag length/count were unbounded, so a long enough
tag (Cyrillic is 2 bytes/char in UTF-8) pushed `tasks_tag:<tag>`
callback_data past Telegram's 64-byte limit and made the whole tags menu
keyboard unrenderable — and enough distinct tags did the same via sheer
button count. Lives under bot/services/ (not bot/utils/, where the
extraction function itself is defined) because pytest.ini's `testpaths`
only covers bot/services and bot/keyboards — this is where the rest of the
suite's cross-module regression tests already live (see
test_tags_and_priority.py for the DAO-level counterpart)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.reminders import callback_tasks_filter_by_tag
from bot.keyboards.inline import get_tags_menu_keyboard
from bot.utils.tags import MAX_TAG_LENGTH, MAX_TAGS_PER_REMINDER, extract_tags_and_priority

_L10N = {"btn_close": "Close"}


def test_long_tag_is_truncated_not_dropped() -> None:
    long_tag = "дом" * 100  # 300 Cyrillic chars, way over any reasonable limit
    clean, tags_csv, _ = extract_tags_and_priority(f"buy milk #{long_tag}")
    assert clean == "buy milk"
    assert tags_csv is not None
    stored = tags_csv.split(",")
    assert len(stored) == 1
    assert stored[0] == long_tag[:MAX_TAG_LENGTH]
    assert len(stored[0]) <= MAX_TAG_LENGTH


def test_tag_count_per_reminder_is_capped() -> None:
    text = "task " + " ".join(f"#tag{i}" for i in range(50))
    _, tags_csv, _ = extract_tags_and_priority(text)
    assert tags_csv is not None
    assert len(tags_csv.split(",")) == MAX_TAGS_PER_REMINDER


def test_every_tag_button_callback_data_fits_telegram_limit() -> None:
    # A tag this long could only exist in data written before this fix
    # (extract_tags_and_priority now truncates at MAX_TAG_LENGTH), but the
    # keyboard must stay defensive against it regardless.
    tags = ["дом" * 100, "work", "urgent"]
    markup = get_tags_menu_keyboard(tags, _L10N)
    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode("utf-8")) <= 64


def test_tags_menu_keyboard_caps_button_count() -> None:
    tags = [f"tag{i}" for i in range(100)]
    markup = get_tags_menu_keyboard(tags, _L10N)
    tag_buttons = [
        b for row in markup.inline_keyboard for b in row if b.callback_data.startswith("tasks_tag:")
    ]
    assert 0 < len(tag_buttons) <= 30


def test_tags_menu_keyboard_still_builds_with_many_normal_tags() -> None:
    tags = [f"tag{i}" for i in range(20)]
    markup = get_tags_menu_keyboard(tags, _L10N)
    tag_buttons = [
        b for row in markup.inline_keyboard for b in row if b.callback_data.startswith("tasks_tag:")
    ]
    assert len(tag_buttons) == 20


async def test_filter_by_tag_survives_empty_tag_without_raising() -> None:
    """An empty tag ('tasks_tag:' with nothing after it — e.g. a truncated
    or hand-crafted callback) must not raise; it should behave like any
    other filter with zero matches."""
    reminder_dao = SimpleNamespace(get_reminders_by_tag=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tasks_tag:", message=message, answer=AsyncMock())

    await callback_tasks_filter_by_tag(callback, reminder_dao, user, _L10N)

    reminder_dao.get_reminders_by_tag.assert_awaited_once_with(1, "")
    message.edit_text.assert_awaited_once()
