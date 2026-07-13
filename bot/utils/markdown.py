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


def strip_markdown_escapes(text: str) -> str:
    """Reverse escape_markdown() — for the plain-text (parse_mode=None) fallback
    used when a Markdown-formatted send fails to parse, so the user sees the
    original characters instead of stray backslashes."""
    return re.sub(r"\\([_*`\[])", r"\1", text)

