from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


async def test_nag_followup_replaces_previous_message() -> None:
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        delete_message=AsyncMock(),
    )
    bot.send_message.return_value = SimpleNamespace(
        chat=SimpleNamespace(id=777),
        message_id=9001,
    )
    scheduler_service = SchedulerService(
        scheduler=AsyncIOScheduler(),
        bot=bot,
        session_pool=None,
    )
    reminder = SimpleNamespace(
        id=1,
        user_id=777,
        reminder_text="Stand up and stretch",
        is_nagging=True,
        last_nag_chat_id=777,
        last_nag_message_id=1337,
    )

    await scheduler_service._send_or_replace_nag_message(
        reminder=reminder,
        l10n={"reminder_prefix": "🔔 "},
        keyboard=None,
        is_nagging_execution=True,
    )

    bot.delete_message.assert_awaited_once_with(chat_id=777, message_id=1337)
    bot.send_message.assert_awaited_once()
    assert reminder.last_nag_chat_id == 777
    assert reminder.last_nag_message_id == 9001


async def test_non_nagging_send_clears_tracking() -> None:
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        delete_message=AsyncMock(),
    )
    bot.send_message.return_value = SimpleNamespace(
        chat=SimpleNamespace(id=55),
        message_id=66,
    )
    scheduler_service = SchedulerService(
        scheduler=AsyncIOScheduler(),
        bot=bot,
        session_pool=None,
    )
    reminder = SimpleNamespace(
        id=2,
        user_id=55,
        reminder_text="Call mom",
        is_nagging=False,
        last_nag_chat_id=55,
        last_nag_message_id=111,
    )

    await scheduler_service._send_or_replace_nag_message(
        reminder=reminder,
        l10n={"reminder_prefix": "🔔 "},
        keyboard=None,
        is_nagging_execution=True,
    )

    bot.delete_message.assert_awaited_once_with(chat_id=55, message_id=111)
    bot.send_message.assert_awaited_once()
    assert reminder.last_nag_chat_id is None
    assert reminder.last_nag_message_id is None
