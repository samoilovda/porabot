"""Regression test: a rate-limited SetMyCommands call must not crash startup.

The bug: bot/__main__.py's _set_bot_commands called bot.set_my_commands once
per language with no error handling. SetMyCommands has a tight per-bot
flood-control window; once tripped (e.g. by a crash-restart loop hammering
it on every boot), every subsequent boot's call raised TelegramRetryAfter,
which propagated out of main() and crashed the whole process — turning one
transient rate limit into an indefinite outage, since nothing else in main()
could run (scheduler, polling) once this raised.
"""

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SetMyCommands

from bot.__main__ import _set_bot_commands


class _FakeBot:
    def __init__(self, fail_langs: set) -> None:
        self.fail_langs = fail_langs
        self.calls = []

    async def set_my_commands(self, commands, language_code=None):
        self.calls.append(language_code)
        if language_code in self.fail_langs:
            raise TelegramRetryAfter(
                method=SetMyCommands(commands=commands, language_code=language_code),
                message="Too Many Requests: retry after 840",
                retry_after=840,
            )


async def test_flood_control_on_one_language_does_not_stop_the_rest() -> None:
    bot = _FakeBot(fail_langs={"en"})
    await _set_bot_commands(bot)
    assert bot.calls == [None, "en", "ru", "es"]


async def test_flood_control_on_every_language_does_not_raise() -> None:
    bot = _FakeBot(fail_langs={None, "en", "ru", "es"})
    await _set_bot_commands(bot)  # must not raise
    assert bot.calls == [None, "en", "ru", "es"]


async def test_a_non_telegram_error_still_propagates() -> None:
    class _BrokenBot(_FakeBot):
        async def set_my_commands(self, commands, language_code=None):
            raise RuntimeError("boom")

    try:
        await _set_bot_commands(_BrokenBot(fail_langs=set()))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError to propagate")
