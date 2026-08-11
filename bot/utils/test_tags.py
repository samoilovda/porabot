"""4.3: #tag and !priority extraction (bot/utils/tags.py)."""

from bot.utils.tags import extract_tags_and_priority, format_tags, priority_glyph


def test_extracts_single_tag_and_strips_it() -> None:
    clean, tags, priority = extract_tags_and_priority("купить молоко #дом")
    assert clean == "купить молоко"
    assert tags == "дом"
    assert priority is None


def test_extracts_multiple_tags_deduplicated_and_lowercased() -> None:
    clean, tags, priority = extract_tags_and_priority("task #Work #urgent #work")
    assert clean == "task"
    assert tags == "work,urgent"
    assert priority is None


def test_extracts_priority_marker() -> None:
    clean, tags, priority = extract_tags_and_priority("call the bank !1")
    assert clean == "call the bank"
    assert priority == 1
    assert tags is None


def test_extracts_tags_and_priority_together_in_any_order() -> None:
    clean, tags, priority = extract_tags_and_priority("купить молоко #дом !2")
    assert clean == "купить молоко"
    assert tags == "дом"
    assert priority == 2


def test_priority_marker_must_be_standalone_token() -> None:
    # "!2" glued to a word (no boundary) must not be treated as a priority.
    clean, tags, priority = extract_tags_and_priority("wow!2 amazing")
    assert priority is None
    assert clean == "wow!2 amazing"


def test_only_first_priority_marker_used() -> None:
    clean, tags, priority = extract_tags_and_priority("task !1 also !2")
    assert priority == 1
    assert clean == "task also !2"


def test_no_markers_returns_original_text_unchanged() -> None:
    clean, tags, priority = extract_tags_and_priority("plain task text")
    assert clean == "plain task text"
    assert tags is None
    assert priority is None


def test_empty_text() -> None:
    clean, tags, priority = extract_tags_and_priority("")
    assert clean == ""
    assert tags is None
    assert priority is None


def test_priority_glyph_known_and_unknown() -> None:
    assert priority_glyph(1) == "🔴"
    assert priority_glyph(2) == "🟠"
    assert priority_glyph(3) == "🟡"
    assert priority_glyph(None) == ""
    assert priority_glyph(4) == ""


def test_format_tags_round_trip() -> None:
    assert format_tags("дом,работа") == "#дом #работа"
    assert format_tags(None) == ""
    assert format_tags("") == ""
