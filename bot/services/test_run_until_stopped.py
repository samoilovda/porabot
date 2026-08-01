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
