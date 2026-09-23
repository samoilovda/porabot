"""docs/audits/2026-09-22-audit.md#a-29 — both of Telegram's Markdown
flavors (legacy "Markdown" and "MarkdownV2") use a SINGLE asterisk for
bold (`*bold*`), unlike common Markdown elsewhere that uses `**bold**`.
Every lexicon string used to write bold as `**text**` — Telegram's parser
reads that as an EMPTY bold span followed by literal "text**", so users
saw stray asterisks around every supposedly-bold word throughout the bot
(the main menu, settings, habit prompts, daily briefs, ...).
"""

from bot.lexicon.en import EN
from bot.lexicon.es import ES
from bot.lexicon.ru import RU


def _all_double_asterisk_hits(lexicon: dict) -> list[str]:
    return [key for key, value in lexicon.items() if isinstance(value, str) and "**" in value]


def test_no_lexicon_string_uses_double_asterisk_bold() -> None:
    for name, lexicon in (("RU", RU), ("EN", EN), ("ES", ES)):
        hits = _all_double_asterisk_hits(lexicon)
        assert not hits, f"{name} has \"**\" (should be single \"*\") in: {hits}"
