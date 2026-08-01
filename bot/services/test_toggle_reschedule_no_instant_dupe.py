import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_toggle_nagging_on_past_one_off_reminder_removes_job_instead_of_firing_now() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)
    scheduler.add_job = Mock(wraps=scheduler.add_job)

    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    reminder = SimpleNamespace(
        id=55,
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        execution_time=past_time,
    )
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock()),
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = SimpleNamespace(
        data="edit_toggle_nagging_55",
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), chat=SimpleNamespace(id=1), message_id=1),
        answer=AsyncMock(),
    )
    l10n = {
        "item_not_found": "not found",
        "status_on": "ON",
        "status_off": "OFF",
        "btn_repeat_prefix": "Repeat:",
        "btn_nagging_prefix": "{icon} Nagging:",
        "btn_nagging_repeats_prefix": "Nag repeats: {count}",
        "btn_delete": "Delete",
        "btn_cancel": "Cancel",
        "repeat_none": "None",
    }

    await reminders_module.callback_edit_nagging(callback, reminder_dao, service, user, l10n)

    # No date-job was scheduled for the already-past execution_time — an
    # instant duplicate notification must not fire from a simple toggle.
    scheduler.add_job.assert_not_called()
    assert scheduler.get_job("55") is None


async def test_toggle_repeat_on_past_recurring_reminder_advances_to_future_occurrence() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)

    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    reminder = SimpleNamespace(
        id=77,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        execution_time=past_time,
    )
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock()),
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = SimpleNamespace(
        data="edit_toggle_repeat_77",
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), chat=SimpleNamespace(id=1), message_id=1),
        answer=AsyncMock(),
    )
    l10n = {
        "item_not_found": "not found",
        "status_on": "ON",
        "status_off": "OFF",
        "btn_repeat_prefix": "Repeat:",
        "btn_nagging_prefix": "{icon} Nagging:",
        "btn_nagging_repeats_prefix": "Nag repeats: {count}",
        "btn_delete": "Delete",
        "btn_cancel": "Cancel",
        "repeat_none": "None",
        "repeat_day": "Daily",
        "repeat_weekdays": "Weekdays",
        "repeat_week": "Weekly",
    }

    await reminders_module.callback_edit_repeat(callback, reminder_dao, service, user, l10n)

    job = scheduler.get_job("77")
    assert job is not None
    assert job.trigger.run_date > datetime.now(timezone.utc)
    assert reminder.execution_time > past_time


async def test_enabling_repeat_on_a_past_plain_task_creates_a_future_occurrence() -> None:
    """P0-2: a brand-new ordinary task is always created with
    is_recurring=False. Turning recurrence on for one that's already overdue
    (e.g. the user is catching up on a missed reminder) must schedule the
    NEXT occurrence, not silently do nothing or fire immediately."""
    reminders_module = _load_module("bot/handlers/reminders.py")

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)

    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    reminder = SimpleNamespace(
        id=88,
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        execution_time=past_time,
    )
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock()),
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = SimpleNamespace(
        data="edit_toggle_repeat_88",
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), chat=SimpleNamespace(id=1), message_id=1),
        answer=AsyncMock(),
    )
    l10n = {
        "item_not_found": "not found",
        "status_on": "ON",
        "status_off": "OFF",
        "btn_repeat_prefix": "Repeat:",
        "btn_nagging_prefix": "{icon} Nagging:",
        "btn_nagging_repeats_prefix": "Nag repeats: {count}",
        "btn_delete": "Delete",
        "btn_cancel": "Cancel",
        "repeat_none": "None",
        "repeat_day": "Daily",
        "repeat_weekdays": "Weekdays",
        "repeat_week": "Weekly",
    }

    # First tap: none -> daily.
    await reminders_module.callback_edit_repeat(callback, reminder_dao, service, user, l10n)

    assert reminder.is_recurring is True
    assert reminder.rrule_string == "FREQ=DAILY"
    job = scheduler.get_job("88")
    assert job is not None
    assert job.trigger.run_date > datetime.now(timezone.utc)
    assert reminder.execution_time > past_time


async def test_repeat_toggle_cycles_through_all_options_and_back_to_none() -> None:
    """none -> daily -> weekdays -> weekly -> none, updating both the DB
    fields and the scheduled job at each step."""
    reminders_module = _load_module("bot/handlers/reminders.py")

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=None)

    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
    reminder = SimpleNamespace(
        id=99,
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        execution_time=future_time,
    )
    reminder_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder),
        session=SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock()),
    )
    user = SimpleNamespace(id=1, timezone="UTC")
    callback = SimpleNamespace(
        data="edit_toggle_repeat_99",
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), chat=SimpleNamespace(id=1), message_id=1),
        answer=AsyncMock(),
    )
    l10n = {
        "item_not_found": "not found",
        "status_on": "ON",
        "status_off": "OFF",
        "btn_repeat_prefix": "Repeat:",
        "btn_nagging_prefix": "{icon} Nagging:",
        "btn_nagging_repeats_prefix": "Nag repeats: {count}",
        "btn_delete": "Delete",
        "btn_cancel": "Cancel",
        "repeat_none": "None",
        "repeat_day": "Daily",
        "repeat_weekdays": "Weekdays",
        "repeat_week": "Weekly",
    }

    expected_sequence = [
        (True, "FREQ=DAILY"),
        (True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        (True, "FREQ=WEEKLY"),
        (False, None),
    ]
    for expected_is_recurring, expected_rrule in expected_sequence:
        await reminders_module.callback_edit_repeat(callback, reminder_dao, service, user, l10n)
        assert reminder.is_recurring == expected_is_recurring
        assert reminder.rrule_string == expected_rrule
        job = scheduler.get_job("99")
        if expected_is_recurring:
            assert job is not None
        else:
            # Turning recurrence back off for a still-future one-off must
            # still leave its own upcoming job scheduled (execution_time
            # itself hasn't changed and is still ahead of now).
            assert job is not None
