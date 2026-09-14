"""Regression (REWORK_PLAN_3 4.4): docker-compose's restart: always only
reacts to the process exiting — a polling loop that's alive but hung
(deadlocked, stuck on a network call that never times out) looks identical
to a healthy container from the outside, and was invisible before this.
"""

import asyncio
import importlib.util
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_heartbeat_creates_a_fresh_file(tmp_path) -> None:
    main_module = _load_module("bot/__main__.py")
    heartbeat_path = tmp_path / "heartbeat"
    main_module.config.HEARTBEAT_FILE = str(heartbeat_path)

    main_module._write_heartbeat()

    assert heartbeat_path.exists()
    assert time.time() - os.path.getmtime(heartbeat_path) < 5


def test_write_heartbeat_survives_an_unwritable_path(tmp_path, caplog) -> None:
    """Must not crash the caller (a periodic scheduler job) — a healthcheck
    that can't be written should be logged, not fatal."""
    main_module = _load_module("bot/__main__.py")
    main_module.config.HEARTBEAT_FILE = str(tmp_path / "no-such-dir" / "heartbeat")

    main_module._write_heartbeat()  # must not raise


# ---------------------------------------------------------------------------
# 3.2: HeartbeatMonitor — the file touch alone only proves the process is
# scheduling jobs, not that it can actually reach Telegram. A polling loop
# stuck on a hung network call kept touching the file every minute with
# nothing wrong ever visible from the outside — the exact "alive but dead"
# scenario a healthcheck exists to catch.
# ---------------------------------------------------------------------------

def _make_monitor(main_module, tmp_path, *, get_me):
    main_module.config.HEARTBEAT_FILE = str(tmp_path / "heartbeat")
    bot = SimpleNamespace(get_me=get_me)
    stop_event = asyncio.Event()
    return main_module.HeartbeatMonitor(bot, stop_event, failure_limit=3, timeout_seconds=0.2), stop_event


async def test_successful_connectivity_check_writes_the_file_and_resets_the_counter(tmp_path) -> None:
    main_module = _load_module("bot/__main__.py")
    monitor, stop_event = _make_monitor(main_module, tmp_path, get_me=AsyncMock(return_value=object()))
    monitor.consecutive_failures = 2  # simulate having recovered from a prior blip

    await monitor.check_and_write()

    assert monitor.consecutive_failures == 0
    assert not stop_event.is_set()
    heartbeat_path = tmp_path / "heartbeat"
    assert heartbeat_path.exists()
    assert time.time() - os.path.getmtime(heartbeat_path) < 5


async def test_failed_connectivity_check_does_not_write_the_file(tmp_path) -> None:
    main_module = _load_module("bot/__main__.py")
    monitor, stop_event = _make_monitor(
        main_module, tmp_path, get_me=AsyncMock(side_effect=RuntimeError("network down"))
    )

    await monitor.check_and_write()

    assert monitor.consecutive_failures == 1
    assert not stop_event.is_set()
    assert not (tmp_path / "heartbeat").exists()


async def test_three_consecutive_failures_triggers_graceful_shutdown(tmp_path) -> None:
    main_module = _load_module("bot/__main__.py")
    monitor, stop_event = _make_monitor(
        main_module, tmp_path, get_me=AsyncMock(side_effect=RuntimeError("network down"))
    )

    for expected_count in (1, 2):
        await monitor.check_and_write()
        assert monitor.consecutive_failures == expected_count
        assert not stop_event.is_set()

    await monitor.check_and_write()

    assert monitor.consecutive_failures == 3
    # Same graceful shutdown mechanism a real SIGTERM sets (see 1.1) — not
    # a hard kill.
    assert stop_event.is_set()


async def test_a_timeout_counts_as_a_failure_too(tmp_path) -> None:
    async def _hangs_forever(*args, **kwargs):
        await asyncio.sleep(999)

    main_module = _load_module("bot/__main__.py")
    monitor, stop_event = _make_monitor(main_module, tmp_path, get_me=_hangs_forever)

    await monitor.check_and_write()

    assert monitor.consecutive_failures == 1
    assert not stop_event.is_set()


async def test_recovery_after_failures_resets_the_streak(tmp_path) -> None:
    main_module = _load_module("bot/__main__.py")
    monitor, stop_event = _make_monitor(main_module, tmp_path, get_me=AsyncMock(side_effect=RuntimeError("blip")))
    await monitor.check_and_write()
    await monitor.check_and_write()
    assert monitor.consecutive_failures == 2

    monitor.bot.get_me = AsyncMock(return_value=object())
    await monitor.check_and_write()

    assert monitor.consecutive_failures == 0
    assert not stop_event.is_set()
