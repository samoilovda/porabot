"""1.1: aiogram's own Dispatcher.start_polling() installs signal handlers
for SIGTERM/SIGINT by default — loop.add_signal_handler REPLACES a
previously registered handler for the same signal rather than chaining it,
so this silently overwrote the handlers main() registers itself (the ones
that set stop_event). The result: stop_event was never set by a real
shutdown signal, _run_until_stopped saw polling end on its own without it,
and raised "Polling stopped unexpectedly" on every single deploy / `docker
stop` — a false-alarm crash on ordinary, successful shutdowns.

_start_polling_coro must call start_polling with handle_signals=False so
main()'s own handlers (registered just before this call) are the only
ones — see bot/__main__.py's module-level comment at the call site.
"""

from unittest.mock import MagicMock, call

from bot.__main__ import _start_polling_coro


def test_start_polling_disables_aiogram_own_signal_handlers() -> None:
    dp = MagicMock()
    bot = MagicMock()

    _start_polling_coro(dp, bot)

    assert dp.start_polling.call_args == call(bot, handle_signals=False)
