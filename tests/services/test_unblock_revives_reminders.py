"""B-07 (2026-11-04 audit): a reminder that reached FORBIDDEN_STRIKES_LIMIT
while the user had the bot blocked must start firing again once they
unblock it — previously nothing ever reset forbidden_strikes."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, ChatMemberMember, ChatMemberUpdated
from aiogram.types import User as TgUser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.context import clear_context
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.engine import Base
from bot.database.models import Reminder, User
from bot.handlers.commands import on_my_chat_member
from bot.services.scheduler import FORBIDDEN_STRIKES_LIMIT, SchedulerService


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    clear_context()


def _unblock_event(user_id: int) -> ChatMemberUpdated:
    tg_user = TgUser(id=user_id, is_bot=False, first_name="A")
    return ChatMemberUpdated(
        chat=Chat(id=user_id, type="private"), from_user=tg_user, date=datetime.now(),
        old_chat_member=ChatMemberMember(user=tg_user), new_chat_member=ChatMemberMember(user=tg_user),
    )


async def test_unblock_resets_strikes_and_recreates_the_job(maker) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with maker() as s:
        s.add(User(id=1, username="u", timezone="UTC", bot_blocked_at=now - timedelta(days=2)))
        s.add(User(id=2, username="v", timezone="UTC"))
        await s.flush()
        dead = await ReminderDAO(s).create_reminder(
            1, "daily pill", now + timedelta(hours=3), is_recurring=True, rrule_string="FREQ=DAILY"
        )
        dead.forbidden_strikes = FORBIDDEN_STRIKES_LIMIT
        other = await ReminderDAO(s).create_reminder(2, "other user", now + timedelta(hours=3))
        await s.commit()
        dead_id, other_id = dead.id, other.id

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=maker)
    async with maker() as s:
        user = await s.get(User, 1)
        await on_my_chat_member(_unblock_event(1), UserDAO(s), user, ReminderDAO(s), service)

    async with maker() as s:
        assert (await s.get(Reminder, dead_id)).forbidden_strikes == 0
        assert (await s.get(User, 1)).bot_blocked_at is None
    assert scheduler.get_job(str(dead_id)) is not None
    # the per-user reconcile must not touch other users' reminders
    assert scheduler.get_job(str(other_id)) is None


async def test_unblock_without_injected_services_still_clears_flag() -> None:
    user = SimpleNamespace(id=1, bot_blocked_at=datetime.now())
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    await on_my_chat_member(_unblock_event(1), user_dao, user)
    assert user.bot_blocked_at is None
