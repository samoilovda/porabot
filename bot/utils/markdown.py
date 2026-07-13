"""Markdown helpers."""

import re


_MDV2_SPECIAL_CHARS_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_MD_LEGACY_SPECIAL_CHARS_RE = re.compile(r"([_*`\[])")


def escape_markdown_v2(text: str) -> str:
    """Escape user-controlled text for Telegram MarkdownV2."""
    return _MDV2_SPECIAL_CHARS_RE.sub(r"\\\1", text)


def escape_markdown(text: str) -> str:
    """Escape user-controlled text for Telegram legacy Markdown (parse_mode='Markdown')."""
    return _MD_LEGACY_SPECIAL_CHARS_RE.sub(r"\\\1", text)

