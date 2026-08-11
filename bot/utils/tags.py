"""4.3: `#tag` and `!priority` extraction from a reminder phrase.

Both are parsed out of the *already datetime-stripped* clean text (whatever
the NLP parser left after pulling the date/time out) — see
bot/handlers/reminders.py's _handle_parsed_result, the single place that
turns free text into a Reminder. Deliberately no separate tags table: tags
are stored as a comma-separated, lowercased string on Reminder.tags, which
is enough for the flat "filter tasks by tag" use case this closes.
"""

import re
from typing import Optional

# "#дом", "#work_stuff", "#3" — any run of word characters (unicode-aware,
# so Cyrillic tags work) after a '#'.
_TAG_RE = re.compile(r"#(\w+)", re.UNICODE)

# "!1"/"!2"/"!3" as a standalone token: bounded by whitespace/string edges
# or common punctuation, so "цена!2" or "ой!" don't false-positive.
_PRIORITY_RE = re.compile(r"(?:(?<=\s)|^)!([1-3])(?=\s|$|[.,!?;:])", re.UNICODE)

_PRIORITY_GLYPHS = {1: "🔴", 2: "🟠", 3: "🟡"}


def extract_tags_and_priority(text: str) -> tuple[str, Optional[str], Optional[int]]:
    """Pull `#tag`s and a `!1`-`!3` priority marker out of *text*.

    Returns (clean_text, tags_csv, priority) — clean_text has all matched
    markers removed and whitespace collapsed; tags_csv is a comma-separated
    lowercased, de-duplicated, order-preserving list of tags or None;
    priority is an int in 1..3 or None. Only the FIRST priority marker
    found is used if more than one is present.
    """
    text = text or ""

    tags: list[str] = []
    seen: set[str] = set()
    for m in _TAG_RE.finditer(text):
        tag = m.group(1).lower()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    clean = _TAG_RE.sub("", text)

    priority: Optional[int] = None
    pm = _PRIORITY_RE.search(clean)
    if pm:
        priority = int(pm.group(1))
        clean = clean[: pm.start()] + clean[pm.end() :]

    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    tags_csv = ",".join(tags) if tags else None
    return clean, tags_csv, priority


def priority_glyph(priority: Optional[int]) -> str:
    """Human-facing glyph for a priority level, or "" if unset/invalid."""
    try:
        return _PRIORITY_GLYPHS.get(int(priority), "") if priority is not None else ""
    except (TypeError, ValueError):
        return ""


def format_tags(tags_csv: Optional[str]) -> str:
    """Render a stored tags_csv back into "#tag1 #tag2" for display."""
    if not tags_csv:
        return ""
    return " ".join(f"#{t}" for t in tags_csv.split(",") if t)
