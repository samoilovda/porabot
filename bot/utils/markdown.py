"""Markdown helpers."""

import re


_MDV2_SPECIAL_CHARS_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_MD_LEGACY_SPECIAL_CHARS_RE = re.compile(r"([_*`\[])")


def escape_markdown_v2(text: str) -> str:
    """Escape user-controlled text for Telegram MarkdownV2."""
    return _MDV2_SPECIAL_CHARS_RE.sub(r"\\\1", text)


def escape_markdown_legacy(text: str) -> str:
    """Escape user-controlled text for legacy Telegram Markdown (parse_mode='Markdown').

    Only `_ * \\` [` are special in this mode — escaping the full MarkdownV2 set
    would leave stray backslashes visible since V1 doesn't treat them as escapes.
    """
    return _MD_LEGACY_SPECIAL_CHARS_RE.sub(r"\\\1", text)

