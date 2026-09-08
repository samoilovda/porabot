import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_handler(module_rel_path: str, fn_name: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location(f"test_{fn_name}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, fn_name)


pick_done_reply = _load_handler("bot/handlers/reminders.py", "_pick_done_reply")


def test_pick_done_reply_uses_variant_list() -> None:
    options = [
        "✅ **You go!**",
        "✅ **Great!**",
        "✅ **Nice progress!**",
    ]
    for _ in range(30):
        picked = pick_done_reply({"task_done_replies": options, "task_done_reply": "legacy"})
        assert picked in options


def test_pick_done_reply_falls_back_to_legacy_value() -> None:
    picked = pick_done_reply({"task_done_replies": [], "task_done_reply": "✅ **Legacy**"})
    assert picked == "✅ **Legacy**"
