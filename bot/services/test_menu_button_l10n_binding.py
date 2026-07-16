"""The bottom menu is built from the lexicon but routed by hardcoded strings.

get_main_menu_keyboard() renders KeyboardButton(text=l10n["btn_habits"]), while
the handler that answers it filters on a literal list F.text.in_(["🫧 Привычки",
"🫧 Habits", "🫧 Hábitos"]). Nothing links the two: renaming a label in the
lexicon silently turns that button into dead text that the catch-all handler
then parses as a new task. These tests bind the two sides together.

The existing test_spanish_menu_buttons.py guards the same coupling for Spanish
against _MENU_TEXTS only; this file generalises it to every language, both for
_MENU_TEXTS and for the router filters themselves.
"""

import ast
from pathlib import Path

import pytest

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]

LANGUAGES = ("ru", "en", "es")
MENU_BUTTON_KEYS = ("btn_new_task", "btn_my_tasks", "btn_settings", "btn_habits")
HANDLER_FILES = (
    "bot/handlers/reminders.py",
    "bot/handlers/habits.py",
    "bot/handlers/settings.py",
)


def _labels_for(key: str) -> set[str]:
    return {get_l10n(lang)[key] for lang in LANGUAGES}


def _text_in_filters(module_rel_path: str) -> list[set[str]]:
    """Every `F.text.in_([...])` string-literal list in a handler module.

    Matches on the F.text receiver specifically so that F.data.in_([...])
    callback filters are not picked up.
    """
    tree = ast.parse((ROOT / module_rel_path).read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "in_":
            continue
        receiver = node.func.value
        if not (isinstance(receiver, ast.Attribute) and receiver.attr == "text"):
            continue
        if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple, ast.Set)):
            continue
        elements = node.args[0].elts
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements):
            found.append({e.value for e in elements})
    return found


@pytest.mark.parametrize("key", MENU_BUTTON_KEYS)
def test_menu_button_is_routed_in_every_language(key: str) -> None:
    labels = _labels_for(key)
    all_filters = [f for path in HANDLER_FILES for f in _text_in_filters(path)]

    matching = [f for f in all_filters if f & labels]
    assert matching, f"no F.text.in_(...) filter handles any label of {key}: {sorted(labels)}"

    for f in matching:
        missing = labels - f
        assert not missing, f"filter handling {key} misses translations: {sorted(missing)}"


@pytest.mark.parametrize("key", MENU_BUTTON_KEYS)
def test_menu_button_is_never_parsed_as_task_text(key: str) -> None:
    # _MENU_TEXTS is the catch-all's opt-out list: a label missing here gets
    # parsed as a reminder instead of opening its screen.
    tree = ast.parse((ROOT / "bot/handlers/reminders.py").read_text(encoding="utf-8"))
    menu_texts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_MENU_TEXTS" for t in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call), "_MENU_TEXTS is no longer a frozenset([...]) literal"
            menu_texts = {
                e.value
                for e in call.args[0].elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    assert menu_texts, "could not extract _MENU_TEXTS literals"

    missing = _labels_for(key) - menu_texts
    assert not missing, f"_MENU_TEXTS misses {key} translations: {sorted(missing)}"
