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


def test_recovery_bulk_actions_use_same_limit_as_the_digest() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    missed_recovery_module = _load_module("bot/services/missed_recovery.py")

    assert reminders_module.RECOVERY_DIGEST_LIMIT == missed_recovery_module.RECOVERY_DIGEST_LIMIT
