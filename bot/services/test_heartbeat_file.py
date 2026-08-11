"""Regression (REWORK_PLAN_3 4.4): docker-compose's restart: always only
reacts to the process exiting — a polling loop that's alive but hung
(deadlocked, stuck on a network call that never times out) looks identical
to a healthy container from the outside, and was invisible before this.
"""

import importlib.util
import os
import time
from pathlib import Path

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
