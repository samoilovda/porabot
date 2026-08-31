"""End-to-end coverage of the habit-timezone-migration flow wired into
bot/handlers/settings.py: changing timezone with active habits offers a
migrate-all / pick / leave-as-is choice, and each path updates execution_time
(and reschedules the job) only for the habits actually selected."""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_settings_module():
    module_path = ROOT / "bot/handlers/settings.py"
    spec = importlib.util.spec_from_file_location("test_settings_tzmig", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _habit(reminder_id: int, text: str, hour_utc: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=reminder_id,
        reminder_text=text,
        execution_time=datetime(2026, 1, 15, hour_utc, 0),
        is_fluid_habit=False,
        fluid_planned_date=None,
        fluid_planned_time=None,
        completed_for_execution_time=None,
        habit_active_due_at=None,
        is_nagging=False,
        pending_delete_at=None,
    )


def _state_with(data: dict) -> SimpleNamespace:
    store = dict(data)

    async def get_data():
        return dict(store)

    async def update_data(**kwargs):
        store.update(kwargs)

    return SimpleNamespace(get_data=AsyncMock(side_effect=get_data), update_data=AsyncMock(side_effect=update_data), _store=store)


async def test_set_tz_offers_migration_choice_when_habits_exist() -> None:
    settings = _load_settings_module()
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1, timezone="Europe/Moscow")
    user_dao = SimpleNamespace(update_timezone=AsyncMock())
    habits = [_habit(1, "Zaryadka", 6)]  # 09:00 Moscow
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=habits))
    state = _state_with({})
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="set_tz_America/New_York", message=message, from_user=SimpleNamespace(first_name="Bob"), answer=AsyncMock()
    )

    await settings.callback_set_tz(
        callback=callback, user_dao=user_dao, user=user, l10n=l10n, state=state, reminder_dao=reminder_dao
    )

    user_dao.update_timezone.assert_awaited_once_with(1, "America/New_York")
    message.edit_text.assert_awaited_once()
    text, kwargs = message.edit_text.await_args.args[0], message.edit_text.await_args.kwargs
    assert "reply_markup" in kwargs and kwargs["reply_markup"] is not None
    assert "09:00" in text  # old local time shown in the drift example
    stored = state._store
    assert stored["tzmig_old_tz"] == "Europe/Moscow"
    assert stored["tzmig_new_tz"] == "America/New_York"


async def test_set_tz_skips_offer_when_no_habits() -> None:
    settings = _load_settings_module()
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1, timezone="Europe/Moscow")
    user_dao = SimpleNamespace(update_timezone=AsyncMock())
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=[]))
    state = _state_with({})
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="set_tz_America/New_York", message=message, from_user=SimpleNamespace(first_name="Bob"), answer=AsyncMock()
    )

    await settings.callback_set_tz(
        callback=callback, user_dao=user_dao, user=user, l10n=l10n, state=state, reminder_dao=reminder_dao
    )

    message.edit_text.assert_awaited_once_with(
        l10n["tz_success"].format(tz=settings._format_tz_display_label("America/New_York")), reply_markup=None
    )
    assert "tzmig_old_tz" not in state._store


async def test_tzmig_all_migrates_every_candidate_and_reschedules() -> None:
    settings = _load_settings_module()
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1)
    habit = _habit(1, "Zaryadka", 6)  # 09:00 Moscow / 01:00 New York in January
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=[habit]), get_by_id=AsyncMock(return_value=habit))
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    state = _state_with({"tzmig_old_tz": "Europe/Moscow", "tzmig_new_tz": "America/New_York"})
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tzmig_all", message=message, answer=AsyncMock())

    await settings.callback_tzmig_all(
        callback=callback, user=user, reminder_dao=reminder_dao, scheduler_service=scheduler_service, state=state, l10n=l10n
    )

    scheduler_service.schedule_reminder.assert_called_once()
    assert scheduler_service.schedule_reminder.call_args.args[0] == 1
    # 09:00 local preserved: execution_time moved off its original 06:00 UTC.
    assert habit.execution_time != datetime(2026, 1, 15, 6, 0)
    message.edit_text.assert_awaited_once()
    summary_text = message.edit_text.await_args.args[0]
    assert "Zaryadka" in summary_text
    assert "tzmig_old_tz" not in state._store or state._store["tzmig_old_tz"] is None


async def test_tzmig_none_leaves_execution_time_untouched() -> None:
    settings = _load_settings_module()
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1)
    habit = _habit(1, "Zaryadka", 6)
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=[habit]))
    state = _state_with({"tzmig_old_tz": "Europe/Moscow", "tzmig_new_tz": "America/New_York"})
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tzmig_none", message=message, answer=AsyncMock())

    await settings.callback_tzmig_none(callback=callback, user=user, reminder_dao=reminder_dao, state=state, l10n=l10n)

    assert habit.execution_time == datetime(2026, 1, 15, 6, 0)
    summary_text = message.edit_text.await_args.args[0]
    assert "Zaryadka" in summary_text


async def test_tzmig_toggle_flips_selection_and_apply_migrates_only_selected() -> None:
    settings = _load_settings_module()
    l10n = get_l10n("en")

    user = SimpleNamespace(id=1)
    habit_a = _habit(1, "A", 6)
    habit_b = _habit(2, "B", 7)
    by_id = {1: habit_a, 2: habit_b}
    reminder_dao = SimpleNamespace(
        get_active_habits=AsyncMock(return_value=[habit_a, habit_b]),
        get_by_id=AsyncMock(side_effect=lambda rid: by_id[rid]),
    )
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    state = _state_with({"tzmig_old_tz": "Europe/Moscow", "tzmig_new_tz": "America/New_York"})
    message = SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock())

    # Enter the pick screen: both selected by default.
    callback_pick = SimpleNamespace(data="tzmig_pick", message=message, answer=AsyncMock())
    await settings.callback_tzmig_pick(callback=callback_pick, user=user, reminder_dao=reminder_dao, state=state, l10n=l10n)
    assert set(state._store["tzmig_selected"]) == {1, 2}

    # Untoggle habit B.
    callback_toggle = SimpleNamespace(data="tzmig_toggle_2", message=message, answer=AsyncMock())
    await settings.callback_tzmig_toggle(callback=callback_toggle, user=user, reminder_dao=reminder_dao, state=state, l10n=l10n)
    assert set(state._store["tzmig_selected"]) == {1}

    # Apply: only A gets migrated.
    callback_apply = SimpleNamespace(data="tzmig_apply", message=message, answer=AsyncMock())
    await settings.callback_tzmig_apply(
        callback=callback_apply, user=user, reminder_dao=reminder_dao, scheduler_service=scheduler_service, state=state, l10n=l10n
    )

    scheduler_service.schedule_reminder.assert_called_once()
    assert scheduler_service.schedule_reminder.call_args.args[0] == 1
    assert habit_a.execution_time != datetime(2026, 1, 15, 6, 0)
    assert habit_b.execution_time == datetime(2026, 1, 15, 7, 0)
