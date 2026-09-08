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


def _forwarded_task_state_filter():
    reminders_module = _load_module("bot/handlers/reminders.py")
    from aiogram.filters.state import StateFilter

    handler = next(
        h for h in reminders_module.router.message.handlers
        if h.callback.__name__ == "handle_forwarded_task"
    )
    state_filters = [f.callback for f in handler.filters if isinstance(f.callback, StateFilter)]
    assert len(state_filters) == 1, "expected exactly one StateFilter on handle_forwarded_task"
    return state_filters[0]


async def test_forwarded_task_handler_only_matches_when_no_fsm_state_active() -> None:
    state_filter = _forwarded_task_state_filter()

    assert await state_filter(obj=None, raw_state=None) is True
    assert await state_filter(obj=None, raw_state="ReminderWizard:waiting_for_nag_limit") is False
    assert await state_filter(obj=None, raw_state="ReminderWizard:waiting_for_done_note") is False
