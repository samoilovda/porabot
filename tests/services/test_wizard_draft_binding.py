"""Audit 2026-11-18 #01: time/confirm buttons are bound to the draft they were
shown for. A button left in an older message must not save or change
anything for a newer draft; old-format buttons (no draft token) are inert.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import reminders_wizard as wiz
from bot.handlers.reminders_shared import _handle_parsed_result
from bot.keyboards.inline import get_parse_confirmation_keyboard, get_time_selection_keyboard
from bot.lexicon import get_l10n
from bot.states.reminder import ReminderWizard

L10N = get_l10n("en")


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _user():
    return SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)


def _msg():
    return SimpleNamespace(chat=SimpleNamespace(id=1), text="", answer=AsyncMock())


def _result(text, dt=None, date_only=False, confidence=0.4):
    return SimpleNamespace(
        clean_text=text, parsed_datetime=dt, confidence=confidence,
        parse_source="x", rrule_string=None, date_only=date_only,
    )


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _cb(data, message=None):
    return SimpleNamespace(
        data=data, answer=AsyncMock(),
        message=message or SimpleNamespace(
            delete=AsyncMock(), edit_text=AsyncMock(), edit_reply_markup=AsyncMock(), answer=AsyncMock(),
        ),
    )


def _env():
    dao = SimpleNamespace(create_reminder=AsyncMock(), get_owned=AsyncMock(), session=SimpleNamespace(commit=AsyncMock()))
    sched = SimpleNamespace(schedule_reminder=lambda *a, **k: None)
    return dao, sched


async def _draft_a_keyboard(state):
    dao, sched = _env()
    msg = _msg()
    day = datetime.now() + timedelta(days=3)
    await _handle_parsed_result(
        msg, state, _user(), L10N, _result("task A", day.replace(hour=0, minute=0, second=0, microsecond=0), True),
        dao, sched,
    )
    return msg.answer.await_args.kwargs["reply_markup"]


async def test_old_draft_button_does_not_touch_new_draft() -> None:
    state = _state()
    kb_a = await _draft_a_keyboard(state)
    morning_a = _buttons(kb_a)[0]
    assert morning_a.callback_data.startswith("tf:")

    # New draft B (no date) replaces A in the FSM.
    dao, sched = _env()
    await _handle_parsed_result(_msg(), state, _user(), L10N, _result("task B"), dao, sched)
    before = await state.get_data()
    assert before["text"] == "task B"

    cb = _cb(morning_a.callback_data)
    await wiz.callback_time_selected(cb, state, _user(), L10N, dao, sched)

    cb.answer.assert_awaited_once_with(L10N["context_expired"], show_alert=True)
    dao.create_reminder.assert_not_awaited()
    cb.message.delete.assert_not_awaited()
    cb.message.edit_reply_markup.assert_awaited_once()
    assert await state.get_data() == before
    assert await state.get_state() == ReminderWizard.choosing_time.state


async def test_current_draft_button_saves() -> None:
    state = _state()
    kb_a = await _draft_a_keyboard(state)
    morning_a = _buttons(kb_a)[0]
    dao, sched = _env()
    dao.create_reminder.return_value = SimpleNamespace(
        id=1, reminder_text="task A", is_recurring=False, is_nagging=False, rrule_string=None,
    )

    cb = _cb(morning_a.callback_data)
    await wiz.callback_time_selected(cb, state, _user(), L10N, dao, sched)

    dao.create_reminder.assert_awaited_once()
    assert dao.create_reminder.await_args.kwargs["text"] == "task A"


@pytest.mark.parametrize(
    "legacy", ["time_delta_60", "time_fixed_2030-01-01T09:00:00+00:00", "time_tomorrow", "time_manual"],
)
async def test_legacy_time_buttons_are_inert(legacy) -> None:
    state = _state()
    await _draft_a_keyboard(state)
    before = await state.get_data()
    cb = _cb(legacy)

    await wiz.callback_legacy_wizard_button(cb, L10N)

    cb.answer.assert_awaited_once_with(L10N["context_expired"], show_alert=True)
    assert await state.get_data() == before


async def test_parse_confirm_bound_to_draft() -> None:
    state = _state()
    dao, sched = _env()
    await state.set_state(ReminderWizard.confirming_parse)
    await state.update_data(draft_id="cur00001", text="t", execution_time="2030-01-01T09:00:00+00:00")

    stale = _cb("pc:old00000:y")
    await wiz.callback_parse_confirm_yes(stale, state, _user(), L10N, dao, sched)
    stale.answer.assert_awaited_once_with(L10N["context_expired"], show_alert=True)
    stale.message.delete.assert_not_awaited()
    dao.create_reminder.assert_not_awaited()

    stale_cancel = _cb("pc:old00000:x")
    await wiz.callback_parse_confirm_cancel(stale_cancel, state, L10N)
    assert await state.get_state() == ReminderWizard.confirming_parse.state

    stale_pick = _cb("pc:old00000:t")
    await wiz.callback_parse_confirm_pick_time(stale_pick, state, _user(), L10N)
    assert await state.get_state() == ReminderWizard.confirming_parse.state

    ok = _cb("pc:cur00001:t")
    await wiz.callback_parse_confirm_pick_time(ok, state, _user(), L10N)
    assert await state.get_state() == ReminderWizard.choosing_time.state
    kb = ok.message.edit_text.await_args.kwargs["reply_markup"]
    assert all("cur00001" in b.callback_data for b in _buttons(kb) if b.callback_data != "cancel_wizard")


async def test_missing_draft_in_fsm_rejects() -> None:
    state = _state()
    await state.set_state(ReminderWizard.choosing_time)
    cb = _cb("tm:abcd1234")
    await wiz.callback_time_selected(cb, state, _user(), L10N, None, None)
    cb.answer.assert_awaited_once_with(L10N["context_expired"], show_alert=True)


def test_callback_data_within_telegram_limit() -> None:
    from datetime import date

    draft = "f" * 8
    for lang in ("en", "ru", "es", "pt"):
        l10n = get_l10n(lang)
        kbs = [
            get_time_selection_keyboard("Europe/Moscow", l10n, True, draft_id=draft),
            get_time_selection_keyboard("Europe/Moscow", l10n, False, base_date=date(2099, 12, 31), draft_id=draft),
            get_parse_confirmation_keyboard(l10n, draft_id=draft),
        ]
        for kb in kbs:
            for b in _buttons(kb):
                assert len(b.callback_data.encode()) <= 64
                if b.callback_data != "cancel_wizard":
                    assert draft in b.callback_data


async def test_fixed_time_button_roundtrips_epoch() -> None:
    state = _state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(draft_id="abcd1234", text="x")
    target = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    dao, sched = _env()
    dao.create_reminder.return_value = SimpleNamespace(
        id=1, reminder_text="x", is_recurring=False, is_nagging=False, rrule_string=None,
    )
    cb = _cb(f"tf:abcd1234:{int(target.timestamp())}")
    await wiz.callback_time_selected(cb, state, _user(), L10N, dao, sched)
    saved = dao.create_reminder.await_args.kwargs["execution_time"]
    assert abs((saved - target.replace(tzinfo=None)).total_seconds()) < 1
