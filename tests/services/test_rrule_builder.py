"""3.1: regression tests for the full RRULE builder — the human-readable
renderer over arbitrary RRULE strings, and the weekly custom-weekday /
monthly / last-weekday / end-condition callback flows."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.handlers.reminders import (
    _parse_rrule_parts,
    _rrule_end_label,
    _rrule_text,
    callback_rrb_end_none,
    callback_rrb_endcount_prompt,
    callback_rrb_last_weekday,
    callback_rrb_monthly_prompt,
    callback_rrb_toggle_weekday,
    callback_rrb_weekday_done,
    state_rrb_end_count,
    state_rrb_monthday,
)
from bot.lexicon.ru import RU
from bot.services.scheduler import SchedulerService


def _reminder(**overrides):
    base = dict(
        id=1,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeFSM:
    def __init__(self, data=None):
        self._data = data or {}

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, _state):
        pass

    async def clear(self):
        self._data = {}


def _dao(reminder):
    return SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock()),
    )


def _callback(data):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )


@pytest.mark.parametrize(
    "rrule_string,expected",
    [
        ("FREQ=DAILY", "День"),
        ("FREQ=DAILY;INTERVAL=3", "Каждые 3 дн."),
        ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "Будни"),
        ("FREQ=WEEKLY;BYDAY=SA,SU", "Выходные"),
        ("FREQ=WEEKLY;BYDAY=MO,WE,FR", "Пн, Ср, Пт"),
        ("FREQ=WEEKLY", "Неделя"),
        ("FREQ=MONTHLY;BYMONTHDAY=15", "15 числа месяца"),
        ("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", "Посл. рабочий день месяца"),
        ("FREQ=DAILY;COUNT=5", "День · 5 повторов"),
        ("FREQ=DAILY;UNTIL=20301231", "День · до 31.12.2030"),
    ],
)
def test_rrule_text_renders_arbitrary_rules(rrule_string, expected) -> None:
    reminder = _reminder(rrule_string=rrule_string)
    assert _rrule_text(reminder, RU) == expected


def test_rrule_text_non_recurring_is_none_label() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    assert _rrule_text(reminder, RU) == RU["repeat_none"]


def test_parse_rrule_parts_roundtrips_flat_string() -> None:
    parts = _parse_rrule_parts("FREQ=MONTHLY;BYMONTHDAY=5;COUNT=10")
    assert parts == {"FREQ": "MONTHLY", "BYMONTHDAY": "5", "COUNT": "10"}


async def test_last_weekday_of_month_sets_expected_rrule() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    callback = _callback("rrb_lastwd_1")

    await callback_rrb_last_weekday(callback, dao, service, SimpleNamespace(id=1, timezone="UTC"), RU)

    assert reminder.rrule_string == "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"
    assert reminder.is_recurring is True


async def test_weekday_toggle_and_done_builds_byday_rule() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    state = FakeFSM()
    user = SimpleNamespace(id=1, timezone="UTC")

    await callback_rrb_toggle_weekday(_callback("rrb_wd_1_MO"), state, dao, user, RU)
    await callback_rrb_toggle_weekday(_callback("rrb_wd_1_WE"), state, dao, user, RU)
    await callback_rrb_weekday_done(_callback("rrb_wddone_1"), state, dao, service, user, RU)

    assert reminder.rrule_string == "FREQ=WEEKLY;BYDAY=MO,WE"
    assert reminder.is_recurring is True


async def test_weekday_done_with_no_selection_rejects() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    state = FakeFSM()
    user = SimpleNamespace(id=1, timezone="UTC")

    await callback_rrb_weekday_done(_callback("rrb_wddone_1"), state, dao, service, user, RU)

    assert reminder.rrule_string is None
    assert reminder.is_recurring is False


async def test_monthday_prompt_flow_sets_bymonthday() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    state = FakeFSM()
    user = SimpleNamespace(id=1, timezone="UTC")

    await callback_rrb_monthly_prompt(_callback("rrb_monthly_1"), state, dao, user, RU)
    await state.update_data(rrb_reminder_id=1)
    message = SimpleNamespace(text="15", answer=AsyncMock())
    await state_rrb_monthday(message, state, dao, service, user, RU)

    assert reminder.rrule_string == "FREQ=MONTHLY;BYMONTHDAY=15"
    assert reminder.is_recurring is True


async def test_monthday_invalid_input_rejected() -> None:
    reminder = _reminder(is_recurring=False, rrule_string=None)
    dao = _dao(reminder)
    state = FakeFSM(data={"rrb_reminder_id": 1})
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    user = SimpleNamespace(id=1, timezone="UTC")
    message = SimpleNamespace(text="99", answer=AsyncMock())

    await state_rrb_monthday(message, state, dao, service, user, RU)

    assert reminder.rrule_string is None
    message.answer.assert_awaited()


async def test_end_condition_count_layers_on_existing_rule() -> None:
    reminder = _reminder(is_recurring=True, rrule_string="FREQ=DAILY")
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    state = FakeFSM(data={"rrb_reminder_id": 1})
    user = SimpleNamespace(id=1, timezone="UTC")
    message = SimpleNamespace(text="10", answer=AsyncMock())

    await state_rrb_end_count(message, state, dao, service, user, RU)

    assert reminder.rrule_string == "FREQ=DAILY;COUNT=10"
    assert _rrule_end_label(reminder.rrule_string, RU) == "10 повторов"


async def test_end_condition_none_strips_count_and_until() -> None:
    reminder = _reminder(is_recurring=True, rrule_string="FREQ=DAILY;COUNT=10")
    dao = _dao(reminder)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    user = SimpleNamespace(id=1, timezone="UTC")

    await callback_rrb_end_none(_callback("rrb_endnone_1"), dao, service, user, RU)

    assert reminder.rrule_string == "FREQ=DAILY"
