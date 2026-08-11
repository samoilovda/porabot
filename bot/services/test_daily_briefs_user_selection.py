from datetime import datetime, timedelta, timezone

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, dispose_engine, init_db
from bot.database.models import User
from bot.services.daily_briefs import get_users_needing_brief_check


async def test_user_who_completed_their_only_task_today_is_still_selected() -> None:
    """Regression (REWORK_PLAN_3 1.3): a one-off reminder flips to
    status='completed' on mark_done. A user whose only task today was a
    one-off they finished must still be picked up so they get tonight's
    evening brief showing what they accomplished — not silently dropped
    just because nothing of theirs is 'pending' any more.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC", briefs_enabled=True))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            reminder = await reminder_dao.create_reminder(
                user_id=1,
                text="pay rent",
                execution_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
                is_recurring=False,
            )
            await reminder_dao.mark_done(reminder.id)
            await session.commit()

        async with session_pool() as session:
            user_ids = await get_users_needing_brief_check(session)

        assert user_ids == [1]
    finally:
        await dispose_engine(engine)


async def test_user_with_only_stale_completed_task_is_not_selected() -> None:
    """A completion from well outside the recent window must not keep
    pulling the user into every future tick forever — that's the exact
    unbounded scan BUG-C1 originally fixed."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC", briefs_enabled=True))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            reminder = await reminder_dao.create_reminder(
                user_id=1,
                text="old one-off task",
                execution_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30),
                is_recurring=False,
            )
            await reminder_dao.mark_done(reminder.id)
            # Push completed_at well outside the recent-completion window —
            # mark_done always stamps "now", so backdate it directly.
            reminder.completed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            await session.commit()

        async with session_pool() as session:
            user_ids = await get_users_needing_brief_check(session)

        assert user_ids == []
    finally:
        await dispose_engine(engine)


async def test_user_with_pending_reminder_is_still_selected() -> None:
    """Baseline: the original 'has a pending reminder' path must keep working."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        session_pool = create_session_maker(engine)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC", briefs_enabled=True))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            await reminder_dao.create_reminder(
                user_id=1,
                text="future task",
                execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                is_recurring=False,
            )
            await session.commit()

        async with session_pool() as session:
            user_ids = await get_users_needing_brief_check(session)

        assert user_ids == [1]
    finally:
        await dispose_engine(engine)
