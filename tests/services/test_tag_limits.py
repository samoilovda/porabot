"""Regression (fix 1.3): tag length/count were unbounded, so a long enough
tag (Cyrillic is 2 bytes/char in UTF-8) pushed `tasks_tag:<tag>`
callback_data past Telegram's 64-byte limit and made the whole tags menu
keyboard unrenderable — and enough distinct tags did the same via sheer
button count. Lives under bot/services/ (not bot/utils/, where the
extraction function itself is defined) because pytest.ini's `testpaths`
only covers bot/services and bot/keyboards — this is where the rest of the
suite's cross-module regression tests already live (see
test_tags_and_priority.py for the DAO-level counterpart)."""

from bot.utils.tags import MAX_TAG_LENGTH, MAX_TAGS_PER_REMINDER, extract_tags_and_priority


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




async def test_tag_menu_callbacks_never_embed_the_tag_text() -> None:
    """The live tag menu (tag_navigation.py) refers to tags by index in a
    stored context, so even a huge legacy tag can't overflow callback_data."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from bot.handlers.tag_navigation import render_tags

    context = SimpleNamespace(token="abcdef123456", payload_json='{"tags": ["' + "дом" * 100 + '", "work"]}')
    message = SimpleNamespace(edit_text=AsyncMock())
    await render_tags(message, context, {"tags_menu_title": "Tags", "btn_filter_back": "Back"}, 0)
    markup = message.edit_text.await_args.kwargs["reply_markup"]
    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode("utf-8")) <= 64
