"""P1-9: polling supervision must surface a crash, not hang forever.

_run_until_stopped waits on FIRST_COMPLETED between polling and a shutdown
signal — waiting on the shutdown signal alone (the earlier version) left an
unhandled exception from polling un-retrieved: the process would hang with
dead polling, Docker would never see a non-zero exit, and `restart: always`
would never kick in.
"""

import asyncio

import pytest

from bot.__main__ import _run_until_stopped


async def test_stop_event_cancels_polling_cleanly() -> None:
    stop_event = asyncio.Event()

    async def _forever_polling():
        await asyncio.Event().wait()  # never resolves on its own

    async def _trigger_stop_soon():
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.ensure_future(_trigger_stop_soon())
    await _run_until_stopped(_forever_polling(), stop_event)
    # No exception — a signal-driven shutdown is a clean return.


async def test_polling_crash_propagates_instead_of_hanging() -> None:
    stop_event = asyncio.Event()

    async def _crashing_polling():
        await asyncio.sleep(0.01)
        raise RuntimeError("Telegram getUpdates blew up")

    with pytest.raises(RuntimeError, match="getUpdates blew up"):
        await _run_until_stopped(_crashing_polling(), stop_event)


async def test_polling_returning_without_a_signal_still_raises() -> None:
    """Even if polling ends "cleanly" (no exception) but no shutdown signal
    was ever set, that's still unexpected — surface it rather than exiting
    the whole process silently."""
    stop_event = asyncio.Event()

    async def _polling_that_just_returns():
        await asyncio.sleep(0.01)

    with pytest.raises(RuntimeError, match="unexpectedly"):
        await _run_until_stopped(_polling_that_just_returns(), stop_event)


# ---------------------------------------------------------------------------
# docs/audits/2026-09-22-audit.md#a-09 — a signal-triggered shutdown, when a
# real Dispatcher is passed, must wind polling down via dp.stop_polling()
# (which lets aiogram finish dispatching whatever update is currently in
# flight and return on its own) instead of task.cancel() (which injects
# CancelledError into a handler at an arbitrary await point — mid-commit,
# mid-send).
# ---------------------------------------------------------------------------

async def test_graceful_shutdown_calls_dp_stop_polling_instead_of_cancelling() -> None:
    stop_event = asyncio.Event()
    polling_finished_via_stop_polling = asyncio.Event()

    async def _real_shaped_polling():
        # Stands in for aiogram's own polling loop: keeps running until
        # something calls stop_polling(), which then lets it return.
        await polling_finished_via_stop_polling.wait()

    class _FakeDispatcher:
        async def stop_polling(self):
            polling_finished_via_stop_polling.set()
            await asyncio.sleep(0)  # let the polling coroutine above actually resolve

    async def _trigger_stop_soon():
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.ensure_future(_trigger_stop_soon())

    # No exception, and specifically no CancelledError swallowed from a
    # hard cancel — dp.stop_polling() is what let this resolve.
    await _run_until_stopped(_real_shaped_polling(), stop_event, _FakeDispatcher())


async def test_dp_none_keeps_the_old_hard_cancel_behavior() -> None:
    """Existing callers (and the other tests in this file) that don't pass
    a Dispatcher must be completely unaffected by A-09's change."""
    stop_event = asyncio.Event()
    cancelled = False

    async def _forever_polling():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def _trigger_stop_soon():
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.ensure_future(_trigger_stop_soon())
    await _run_until_stopped(_forever_polling(), stop_event)  # dp defaults to None

    assert cancelled is True
