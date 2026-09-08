"""Regression (fix 1.1): the aiohttp server used to start unconditionally,
bound to 0.0.0.0, on every install that upgraded — even ones that never
opted into the .ics feed / Mini App. It must now be opt-in via
WEB_SERVER_ENABLED, and the default bind host must be loopback-only.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_web_server_disabled_by_default_in_config() -> None:
    from bot.config import Settings

    assert Settings.model_fields["WEB_SERVER_ENABLED"].default is False


def test_web_server_host_defaults_to_loopback() -> None:
    from bot.config import Settings

    assert Settings.model_fields["WEB_SERVER_HOST"].default == "127.0.0.1"


async def test_web_server_not_started_when_disabled() -> None:
    main_module = _load_module("bot/__main__.py")
    main_module.config.WEB_SERVER_ENABLED = False

    calls = {"create_app": 0, "start_web_server": 0}

    def fake_create_app(*args, **kwargs):
        calls["create_app"] += 1
        return object()

    async def fake_start_web_server(*args, **kwargs):
        calls["start_web_server"] += 1
        return object()

    main_module.create_app = fake_create_app
    main_module.start_web_server = fake_start_web_server

    runner = await main_module._start_web_server_if_enabled(session_pool=None)

    assert runner is None
    assert calls == {"create_app": 0, "start_web_server": 0}


async def test_web_server_started_when_enabled() -> None:
    main_module = _load_module("bot/__main__.py")
    main_module.config.WEB_SERVER_ENABLED = True
    main_module.config.WEB_SERVER_HOST = "127.0.0.1"
    main_module.config.WEB_SERVER_PORT = 8080

    calls = {"create_app": 0, "start_web_server": 0}
    sentinel_runner = object()

    def fake_create_app(*args, **kwargs):
        calls["create_app"] += 1
        return object()

    async def fake_start_web_server(*args, **kwargs):
        calls["start_web_server"] += 1
        return sentinel_runner

    main_module.create_app = fake_create_app
    main_module.start_web_server = fake_start_web_server

    runner = await main_module._start_web_server_if_enabled(session_pool=None)

    assert runner is sentinel_runner
    assert calls == {"create_app": 1, "start_web_server": 1}
