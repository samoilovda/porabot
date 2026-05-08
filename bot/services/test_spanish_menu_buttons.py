import importlib.util
from pathlib import Path

from bot.lexicon import get_l10n


ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spanish_main_menu_labels_are_not_parsed_as_tasks() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    menu_texts = set(getattr(reminders_module, "_MENU_TEXTS"))
    es = get_l10n("es")

    assert es["btn_new_task"] in menu_texts
    assert es["btn_my_tasks"] in menu_texts
    assert es["btn_settings"] in menu_texts
    assert es["btn_habits"] in menu_texts
