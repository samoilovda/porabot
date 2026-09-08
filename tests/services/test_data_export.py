"""3.3: data export — bot.send_document covering reminders + habit_events +
settings, reachable from Settings next to "Clear all"."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.settings import build_data_export, callback_export_data
from bot.keyboards.inline import get_settings_keyboard
from bot.lexicon.ru import RU


def _user(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        timezone="Europe/Moscow",
        language="ru",
        show_utc_offset=True,
        quiet_hours_enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        briefs_enabled=True,
        morning_brief_time="09:00",
        evening_brief_time="23:00",
        missed_recovery_enabled=True,
        missed_recovery_time="10:00",
        habit_reports_enabled=True,
        habit_report_weekday=6,
        habit_report_time="23:50",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _reminder(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        reminder_text="Drink water",
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_fluid_habit=False,
        fluid_mode=None,
        status="pending",
        is_nagging=True,
        nagging_max_repeats=3,
        habit_streak_current=5,
        habit_streak_best=10,
        fluid_streak_current=0,
        fluid_streak_best=0,
        completed_at=None,
        created_at=datetime(2026, 4, 1, 9, 0, 0),
        pending_delete_at=None,
        tags=None,
        priority=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _event(**overrides) -> SimpleNamespace:
    base = dict(
        reminder_id=1,
        habit_text="Drink water",
        local_date="2026-05-01",
        due_at=datetime(2026, 5, 1, 9, 0, 0),
        outcome="done",
        source="button",
        created_at=datetime(2026, 5, 1, 9, 5, 0),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_export_payload_is_json_serializable_and_covers_all_three_areas() -> None:
    user = _user()
    reminders = [_reminder()]
    events = [_event()]

    payload = build_data_export(user, reminders, events)
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["user"]["timezone"] == "Europe/Moscow"
    assert decoded["reminders"][0]["text"] == "Drink water"
    assert decoded["habit_events"][0]["outcome"] == "done"


def test_export_excludes_reminders_pending_delete() -> None:
    user = _user()
    kept = _reminder(id=1)
    soft_deleted = _reminder(id=2, pending_delete_at=datetime(2026, 5, 1, 9, 0, 5))

    payload = build_data_export(user, [kept, soft_deleted], [])

    ids = [r["id"] for r in payload["reminders"]]
    assert ids == [1]


def test_export_includes_tags_and_priority() -> None:
    """Regression (fix 2.1): tags/priority (4.3) were added to Reminder
    after the export payload (3.3) was written, and never backfilled into
    it — "take out your data" silently dropped exactly what the user
    typed by hand."""
    user = _user()
    reminder = _reminder(tags="дом,покупки", priority=2)

    payload = build_data_export(user, [reminder], [])

    exported = payload["reminders"][0]
    assert exported["tags"] == "дом,покупки"
    assert exported["priority"] == 2


async def test_export_data_sends_a_document() -> None:
    user = _user()
    reminder_dao = SimpleNamespace(get_all=AsyncMock(return_value=[_reminder()]))
    habit_event_dao = SimpleNamespace(get_all=AsyncMock(return_value=[_event()]))
    message = SimpleNamespace(answer_document=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_export_data(callback, user, reminder_dao, habit_event_dao, RU)

    message.answer_document.assert_awaited_once()
    callback.answer.assert_awaited_once()


def test_settings_keyboard_has_export_button_before_clear_all() -> None:
    markup = get_settings_keyboard(RU, show_utc_offset=False)
    callback_datas = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "settings_export_data" in callback_datas
    assert callback_datas.index("settings_export_data") < callback_datas.index("settings_clear_all")
