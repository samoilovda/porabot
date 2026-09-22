"""A-07: every lexicon key that gets sent with parse_mode="MarkdownV2" must
have its own literal reserved characters escaped — {placeholder} substitutions
are filled with already-escaped user data by the caller, but a stray literal
"#"/"."/"!"/etc. baked into the template itself makes Telegram reject the
whole message with "can't parse entities", and the bug is invisible until
that exact key is actually rendered (see docs/audits/2026-09-22-audit.md#a-07:
filter_header_tag and find_truncated_notice broke tag filtering and /find's
truncation notice in every language, unconditionally).
"""

import re

from bot.lexicon import get_l10n

# Every lexicon key this codebase sends with parse_mode="MarkdownV2" (not
# legacy "Markdown", which has a different, smaller reserved set) — see the
# call sites in bot/handlers/reminders_listing.py, reminders_completion.py,
# reminders_shared.py, reminders_snooze.py, menu.py, and
# bot/services/daily_briefs.py isn't included here since it uses parse_mode
# "Markdown", not "MarkdownV2".
_MDV2_KEYS = [
    "tasks_header",
    "tasks_page_indicator",
    "find_results_header",
    "find_truncated_notice",
    "filter_header_today",
    "filter_header_week",
    "filter_header_overdue",
    "filter_header_recurring",
    "filter_header_tag",
    "preview",
    "snoozed_until",
    "brief_items_more",
    "completed_header",
]

# Explicitly NOT included, despite looking like candidates — verified against
# their actual call sites: task_deleted/find_no_results_filter go through
# edit_text()/safe_edit_text() with no parse_mode kwarg, which falls back to
# the Bot's own default (legacy Markdown, set in bot/__main__.py's
# DefaultBotProperties — a much smaller reserved set that doesn't include
# "."); task_restored is only ever passed to callback.answer(), which is a
# plain alert box Telegram never runs through any Markdown parser at all.

# MarkdownV2's full reserved set (Bot API docs): every one of these must be
# backslash-escaped outside of an intentional markup span. "*" and "`" are
# allowed unescaped here — they're the deliberate bold/code markers this
# codebase's templates use around the substituted data.
_RESERVED = set("_[]()~>#+-=|{}.!")


def _unescaped_reserved_chars(template: str) -> set[str]:
    """Reserved chars in *template* that are NOT preceded by a backslash,
    after stripping {placeholder} spans (the caller fills those with
    pre-escaped data, so their own curly braces don't count as template
    literals)."""
    without_placeholders = re.sub(r"\{[a-z_]+\}", "", template)
    bad = set()
    for i, ch in enumerate(without_placeholders):
        if ch in _RESERVED and (i == 0 or without_placeholders[i - 1] != "\\"):
            bad.add(ch)
    return bad


def test_mdv2_lexicon_keys_have_no_unescaped_reserved_chars() -> None:
    failures = []
    for lang in ("ru", "en", "es"):
        l10n = get_l10n(lang)
        for key in _MDV2_KEYS:
            value = l10n.get(key)
            if value is None:
                continue
            bad = _unescaped_reserved_chars(value)
            if bad:
                failures.append(f"{lang}.{key} = {value!r} — unescaped: {sorted(bad)}")
    assert not failures, "\n".join(failures)
