"""U-05 (2026-11-04 audit): a nagging follow-up says which one it is."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.context import clear_context
from bot.lexicon import get_l10n
from bot.services.scheduler import SchedulerService


def _reminder(sent):
    return SimpleNamespace(
        user_id=1, reminder_text="выпить таблетку", is_nagging=True, nagging_max_repeats=3,
        nagging_sent_count=sent, last_nag_chat_id=None, last_nag_message_id=None,
    )


async def _send(is_nag, sent=1):
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(AsyncIOScheduler(), bot=bot, session_pool=None)
    await service._send_or_replace_nag_message(
        reminder=_reminder(sent), l10n=get_l10n("ru"), keyboard=None, is_nagging_execution=is_nag
    )
    clear_context()
    return bot.send_message.await_args.kwargs["text"]


async def test_follow_up_shows_its_number() -> None:
    assert (await _send(True, sent=1)).endswith("Повторное напоминание 2 из 3")


async def test_main_notification_has_no_counter() -> None:
    assert (await _send(False)) == "🔔 выпить таблетку"
