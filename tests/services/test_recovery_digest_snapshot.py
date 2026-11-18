"""Audit 2026-11-18 #03: the recovery digest's "+1h all" / "Done all" buttons
act on the tasks the digest LISTED (a stored snapshot), not on whatever is
overdue when the button is pressed.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, init_db
from bot.database.models import UiContext, User
from bot.handlers.recovery_review import batch_action, recovery_snapshot_button
from bot.handlers.reminders_listing import preview_recovery_snooze_all
from bot.keyboards.inline import get_missed_recovery_keyboard
from bot.lexicon import get_l10n
from bot.services.ui_context import batch_items, create_context

L10N = get_l10n("en")


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/rec.sqlite")
    await init_db(engine)
    maker = create_session_maker(engine)
    async with maker() as session:
        session.add_all([User(id=1, timezone="UTC", language="en"), User(id=2, timezone="UTC", language="en")])
        await session.commit()
        yield session, maker
    await engine.dispose()


def _cb(data):
    return SimpleNamespace(
        data=data, answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


def _overdue():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)


async def _task(dao, text):
    return await dao.create_reminder(user_id=1, text=text, execution_time=_overdue())


async def _digest(session, user, tasks):
    return await create_context(session, user.id, "recovery", {"items": batch_items(tasks, user)})


async def _press(session, data, user, sched):
    """Digest button -> preview -> confirm. Returns the confirm callback."""
    dao, events = ReminderDAO(session), HabitEventDAO(session)
    await recovery_snapshot_button(_cb(data), dao, user, L10N)
    batch = (await session.scalars(select(UiContext).where(UiContext.kind == "batch"))).all()[-1]
    confirm = _cb(f"batch:{batch.token}:apply:0")
    await batch_action(confirm, dao, events, sched, user, L10N)
    return confirm


async def test_snooze_all_from_old_digest_does_not_touch_new_overdue_task(database) -> None:
    session, _ = database
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    a = await _task(dao, "A")
    await session.commit()
    digest = await _digest(session, user, [a])

    await dao.mark_done(a.id)  # A closed after the digest was shown
    b = await _task(dao, "B")  # B became overdue later
    await session.commit()
    b_time = b.execution_time

    sched = Mock()
    confirm = await _press(session, f"recovery_snooze_all:{digest.token}", user, sched)

    assert b.execution_time == b_time
    sched.schedule_reminder.assert_not_called()
    assert L10N["recovery_snooze_all_done"].format(count=0) in confirm.message.edit_text.await_args.args[0]


async def test_changed_task_is_skipped_and_reported(database) -> None:
    session, _ = database
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    a, b = await _task(dao, "A"), await _task(dao, "B")
    await session.commit()
    digest = await _digest(session, user, [a, b])
    b.reminder_text = "B edited after the digest"
    await session.commit()
    b_time = b.execution_time

    sched = Mock()
    confirm = await _press(session, f"recovery_snooze_all:{digest.token}", user, sched)

    assert a.execution_time > datetime.now(timezone.utc).replace(tzinfo=None)
    assert b.execution_time == b_time
    text = confirm.message.edit_text.await_args.args[0]
    assert L10N["recovery_snooze_all_done"].format(count=1) in text
    assert L10N["batch_skipped"].format(count=1) in text


async def test_normal_path_snoozes_everything_shown(database) -> None:
    session, _ = database
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    a, b = await _task(dao, "A"), await _task(dao, "B")
    await session.commit()
    digest = await _digest(session, user, [a, b])

    sched = Mock()
    await _press(session, f"recovery_snooze_all:{digest.token}", user, sched)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert all(now < t.execution_time <= now + timedelta(hours=1, minutes=1) for t in (a, b))
    assert sched.schedule_reminder.call_count == 2


async def test_done_all_uses_snapshot_not_fresh_query(database) -> None:
    session, _ = database
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    a = await _task(dao, "A")
    await session.commit()
    digest = await _digest(session, user, [a])
    b = await _task(dao, "B")
    await session.commit()

    await _press(session, f"recovery_done_all:{digest.token}", user, Mock())

    assert a.status == "completed" and b.status == "pending"


async def test_legacy_button_only_previews_and_changes_nothing(database) -> None:
    session, _ = database
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    a = await _task(dao, "A")
    await session.commit()
    before = a.execution_time

    cb = _cb("recovery_snooze_all")
    await preview_recovery_snooze_all(cb, dao, user, L10N)

    assert a.execution_time == before
    cb.message.edit_text.assert_awaited_once()
    ctx = await session.scalar(select(UiContext).where(UiContext.kind == "batch"))
    assert json.loads(ctx.payload_json)["action"] == "snooze"


async def test_unknown_or_foreign_token_is_refused(database) -> None:
    session, _ = database
    user1, user2 = await session.get(User, 1), await session.get(User, 2)
    dao = ReminderDAO(session)
    a = await _task(dao, "A")
    await session.commit()
    digest = await _digest(session, user1, [a])

    for who, token in ((user1, "deadbeef0000"), (user2, digest.token)):
        cb = _cb(f"recovery_snooze_all:{token}")
        await recovery_snapshot_button(cb, dao, who, L10N)
        cb.answer.assert_awaited_once_with(L10N["context_expired"], show_alert=True)
        cb.message.edit_text.assert_not_awaited()


def test_digest_keyboard_carries_token_within_limit() -> None:
    kb = get_missed_recovery_keyboard(L10N, "a1b2c3d4e5f6")
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "recovery_done_all:a1b2c3d4e5f6" in data and "recovery_snooze_all:a1b2c3d4e5f6" in data
    assert all(len(d.encode()) <= 64 for d in data)


async def test_digest_job_stores_snapshot_and_puts_token_on_buttons(database, monkeypatch) -> None:
    from bot.services import missed_recovery

    session, maker = database
    user = await session.get(User, 1)
    user.missed_recovery_time = "00:00"
    dao = ReminderDAO(session)
    tasks = [await _task(dao, f"T{i}") for i in range(7)]  # more than the 5 shown
    await session.commit()

    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr("bot.context.get_context", lambda: SimpleNamespace(bot=bot, session_pool=maker))
    monkeypatch.setattr(missed_recovery, "notification_policy", lambda *a, **k: "send")

    await missed_recovery.process_missed_task_recovery()

    kb = bot.send_message.await_args.kwargs["reply_markup"]
    token = kb.inline_keyboard[0][0].callback_data.split(":")[1]
    async with maker() as s2:
        ctx = await s2.scalar(select(UiContext).where(UiContext.token == token))
    assert ctx.kind == "recovery" and ctx.user_id == 1
    items = json.loads(ctx.payload_json)["items"]
    assert len(items) == missed_recovery.RECOVERY_DIGEST_LIMIT
    assert {i["id"] for i in items} <= {t.id for t in tasks}
