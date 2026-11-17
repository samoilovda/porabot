"""Startup resilience: a Telegram outage at boot must not crash-loop the
process, and stale persisted periodic jobs must not run alongside their
in-memory replacements."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.__main__ import (
    HeartbeatMonitor,
    _wait_for_telegram,
    _wait_for_telegram_then_set_commands,
    purge_stale_persisted_periodic_jobs,
)


async def test_wait_for_telegram_retries_until_getme_succeeds() -> None:
    bot = SimpleNamespace(me=AsyncMock(side_effect=[RuntimeError("Bad Gateway"), RuntimeError("timeout"), object()]))

    await _wait_for_telegram(bot, asyncio.Event(), initial_delay=0.001, max_delay=0.002)

    assert bot.me.await_count == 3


async def test_wait_for_telegram_returns_without_raising_when_stopped() -> None:
    stop_event = asyncio.Event()
    bot = SimpleNamespace(me=AsyncMock(side_effect=RuntimeError("Bad Gateway")))

    async def _stop_soon() -> None:
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.ensure_future(_stop_soon())
    await asyncio.wait_for(_wait_for_telegram(bot, stop_event, initial_delay=0.001, max_delay=0.002), timeout=2)

    assert bot.me.await_count >= 1


async def test_heartbeat_does_not_trigger_shutdown_while_disabled() -> None:
    stop_event = asyncio.Event()
    bot = SimpleNamespace(get_me=AsyncMock(side_effect=RuntimeError("down")))
    monitor = HeartbeatMonitor(bot, stop_event, failure_limit=2)
    monitor.shutdown_enabled = False

    for _ in range(5):
        await monitor.check_and_write()

    assert not stop_event.is_set()

    monitor.shutdown_enabled = True
    await monitor.check_and_write()
    assert stop_event.is_set()


def _fake_scheduler(memory_ids: list[str], default_ids: list[str]) -> MagicMock:
    jobs = {
        "memory": [SimpleNamespace(id=i) for i in memory_ids],
        "default": [SimpleNamespace(id=i) for i in default_ids],
    }
    scheduler = MagicMock()
    scheduler.get_jobs.side_effect = lambda jobstore=None: jobs[jobstore]
    return scheduler


def test_purge_removes_persisted_copies_of_memory_jobs_and_legacy_ids_only() -> None:
    scheduler = _fake_scheduler(
        memory_ids=["daily_briefs_minutely", "write_heartbeat"],
        default_ids=["daily_briefs_minutely", "write_heartbeat", "hourly_daily_briefs", "142", "nag_7"],
    )

    removed = purge_stale_persisted_periodic_jobs(scheduler)

    assert sorted(removed) == ["daily_briefs_minutely", "hourly_daily_briefs", "write_heartbeat"]
    removed_calls = {c.args[0] for c in scheduler.remove_job.call_args_list}
    assert removed_calls == set(removed)
    assert all(c.kwargs == {"jobstore": "default"} for c in scheduler.remove_job.call_args_list)


def test_purge_is_a_noop_on_a_clean_jobstore() -> None:
    scheduler = _fake_scheduler(memory_ids=["daily_briefs_minutely"], default_ids=["142"])

    assert purge_stale_persisted_periodic_jobs(scheduler) == []
    scheduler.remove_job.assert_not_called()


async def test_bot_commands_are_set_only_after_telegram_is_reachable() -> None:
    calls: list[str] = []

    async def _me():
        calls.append("me")
        if calls.count("me") < 3:
            raise RuntimeError("Bad Gateway")
        return object()

    async def _set_my_commands(*args, **kwargs):
        calls.append("set_my_commands")

    bot = SimpleNamespace(me=_me, set_my_commands=_set_my_commands)

    await _wait_for_telegram_then_set_commands(bot, asyncio.Event(), initial_delay=0.001, max_delay=0.002)

    assert calls[:3] == ["me", "me", "me"]
    assert calls.count("set_my_commands") == 5


async def test_bot_commands_are_skipped_when_stopped_during_wait() -> None:
    stop_event = asyncio.Event()
    stop_event.set()
    bot = SimpleNamespace(me=AsyncMock(side_effect=RuntimeError("Bad Gateway")), set_my_commands=AsyncMock())

    await _wait_for_telegram_then_set_commands(bot, stop_event, initial_delay=0.001, max_delay=0.002)

    bot.set_my_commands.assert_not_awaited()

