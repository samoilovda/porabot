"""Audit 2026-11-18 #08: after the user picked a day ("послезавтра") without an
hour, a re-prompt (unparsable follow-up input, or "that time has passed")
must keep that day instead of falling back to the general now-relative
keyboard.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import bot.keyboards.inline as inline_mod
from bot.handlers import reminders_wizard as wiz
from bot.handlers.reminders_shared import _resolve_time_and_respond, _save_and_show_edit
from bot.lexicon import get_l10n
from bot.states.reminder import ReminderWizard

L10N = get_l10n("en")
USER = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _msg(text=""):
    return SimpleNamespace(chat=SimpleNamespace(id=1), text=text, answer=AsyncMock())


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _fixed(markup):
    return [b for b in _buttons(markup) if b.callback_data.startswith("tf:")]


def _freeze(monkeypatch, hour: int) -> datetime:
    now = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(inline_mod, "_now_utc", lambda: now)
    return now


def _day_only(day: date):
    return SimpleNamespace(
        parsed_datetime=datetime(day.year, day.month, day.day), date_only=True, confidence=0.4,
    )


async def _pick_day(state, day: date) -> None:
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="task")
    await _resolve_time_and_respond(
        _msg("day"), state, USER, L10N, _day_only(day), SimpleNamespace(), SimpleNamespace(), "task",
    )


async def _unparsable_input(monkeypatch, state):
    monkeypatch.setattr(wiz.parser, "parse", AsyncMock(return_value=SimpleNamespace(parsed_datetime=None)))
    msg = _msg("абракадабра")
    await wiz.state_choosing_time_text_input(msg, state, USER, L10N, SimpleNamespace(), SimpleNamespace())
    return msg.answer.await_args


async def test_unparsable_input_keeps_picked_day(monkeypatch) -> None:
    now = _freeze(monkeypatch, 10)
    day = (now + timedelta(days=2)).date()
    state = _state()
    await _pick_day(state, day)

    call = await _unparsable_input(monkeypatch, state)

    kb = call.kwargs["reply_markup"]
    assert not any(b.callback_data.startswith("td:") for b in _buttons(kb))  # no "+15m" etc.
    fixed = _fixed(kb)
    assert len(fixed) == 4
    assert {datetime.fromtimestamp(int(b.callback_data.split(":")[2]), timezone.utc).date() for b in fixed} == {day}
    assert day.strftime("%d.%m") in call.args[0]


async def test_button_after_retry_saves_on_picked_day(monkeypatch) -> None:
    now = _freeze(monkeypatch, 10)
    day = (now + timedelta(days=2)).date()
    state = _state()
    await _pick_day(state, day)
    kb = (await _unparsable_input(monkeypatch, state)).kwargs["reply_markup"]

    dao = SimpleNamespace(
        create_reminder=AsyncMock(return_value=SimpleNamespace(
            id=1, reminder_text="task", is_recurring=False, is_nagging=False, rrule_string=None,
        )),
        session=SimpleNamespace(commit=AsyncMock()),
    )
    cb = SimpleNamespace(
        data=_fixed(kb)[0].callback_data, answer=AsyncMock(),
        message=SimpleNamespace(delete=AsyncMock(), answer=AsyncMock(), chat=SimpleNamespace(id=1)),
    )
    await wiz.callback_time_selected(cb, state, USER, L10N, dao, SimpleNamespace(schedule_reminder=lambda *a, **k: None))

    assert dao.create_reminder.await_args.kwargs["execution_time"].date() == day


async def test_past_time_retry_keeps_only_remaining_slots_today(monkeypatch) -> None:
    now = _freeze(monkeypatch, 15)  # slots 09:00 and 14:00 are gone, 19:00 and 23:00 remain
    state = _state()
    await _pick_day(state, now.date())
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.update_data(execution_time=past.isoformat())
    msg = _msg()

    await _save_and_show_edit(msg, state, L10N, USER, SimpleNamespace(), SimpleNamespace())

    kb = msg.answer.await_args.kwargs["reply_markup"]
    hours = [datetime.fromtimestamp(int(b.callback_data.split(":")[2]), timezone.utc).hour for b in _fixed(kb)]
    assert hours == [19, 23]
    assert not any(b.callback_data.startswith("td:") for b in _buttons(kb))
    assert L10N["time_in_past"] in msg.answer.await_args.args[0]


async def test_today_with_no_slots_left_falls_back_and_clears_day(monkeypatch) -> None:
    now = _freeze(monkeypatch, 23)  # 23:00 slot is < 1 minute away at best: here exactly now -> gone
    state = _state()
    await _pick_day(state, now.date())
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.update_data(execution_time=past.isoformat())
    msg = _msg()

    await _save_and_show_edit(msg, state, L10N, USER, SimpleNamespace(), SimpleNamespace())

    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert any(b.callback_data.startswith("td:") for b in _buttons(kb))
    assert (await state.get_data()).get("time_base_date") is None


async def test_day_in_the_past_falls_back_and_clears_day(monkeypatch) -> None:
    now = _freeze(monkeypatch, 10)
    state = _state()
    await _pick_day(state, (now - timedelta(days=3)).date())

    call = await _unparsable_input(monkeypatch, state)

    assert any(b.callback_data.startswith("td:") for b in _buttons(call.kwargs["reply_markup"]))
    assert (await state.get_data()).get("time_base_date") is None


async def test_new_draft_does_not_inherit_old_day(monkeypatch) -> None:
    from bot.handlers.reminders_shared import start_draft

    now = _freeze(monkeypatch, 10)
    state = _state()
    await _pick_day(state, (now + timedelta(days=2)).date())
    await start_draft(state)
    assert (await state.get_data()).get("time_base_date") is None
