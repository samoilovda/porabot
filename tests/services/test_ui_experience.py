"""Database-backed acceptance checks for the 2026-10-18 UI/UX work."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, init_db
from bot.database.models import HabitEvent, Reminder, UiContext, User
from bot.handlers.experience import (
    apply_notifications,
    batch_action,
    begin_batch,
    open_list,
    rename_input,
)
from bot.handlers.habits import HabitState, state_habit_time
from bot.handlers.settings.locale_time import _resolve_timezone_candidate, callback_set_tz, callback_timezone_apply
from bot.lexicon import get_l10n
from bot.services.habit_completion import set_daily_completion
from bot.services.scheduler import SchedulerService
from bot.services.ui_context import create_context, get_context
from bot.services.user_data import UserDataService
from bot.utils.time_ext import next_occurrence_utc
from bot.utils.timezone import resolve_timezone

RU = get_l10n("ru")


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/ui.sqlite")
    await init_db(engine)
    await init_db(engine)  # Refreshing a prepared database must remain repeatable.
    maker = create_session_maker(engine)
    async with maker() as session:
        session.add_all([User(id=1, timezone="UTC", language="ru"), User(id=2, timezone="UTC", language="en")])
        await session.commit()
        yield session, maker
    await engine.dispose()


def fsm():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))


def message():
    return SimpleNamespace(
        text="List",
        chat=SimpleNamespace(id=1),
        message_id=1,
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )


def callback(data, msg=None):
    cb = CallbackQuery.model_construct(
        id="local", data=data, message=msg or message(), from_user=SimpleNamespace(id=1, first_name="Test")
    )
    # Copying a real aiogram model in handlers preserves its injected message.
    return SimpleCallback(cb)


class SimpleCallback:
    def __init__(self, cb):
        self.data, self.message, self.from_user = cb.data, cb.message, cb.from_user
        self.answer = AsyncMock()

    def model_copy(self, update):
        other = SimpleCallback(
            CallbackQuery.model_construct(data=self.data, message=self.message, from_user=self.from_user)
        )
        for key, value in update.items():
            setattr(other, key, value)
        other.answer = self.answer
        return other


async def user_dao(session):
    return await session.get(User, 1), ReminderDAO(session), HabitEventDAO(session)


async def create(dao, *, text="Task", when=None, habit=False, fluid=False):
    return await dao.create_reminder(
        user_id=1,
        text=text,
        execution_time=when or datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2),
        is_recurring=habit,
        is_habit=habit,
        is_fluid_habit=fluid,
        rrule_string="FREQ=DAILY" if habit and not fluid else None,
    )


async def test_context_survives_new_session_and_is_owner_scoped(database):
    session, maker = database
    context = await create_context(session, 1, "list", {"kind": "find", "query": "длинный запрос " * 30})
    view_id = context.token
    async with maker() as fresh:
        assert json.loads((await get_context(fresh, view_id, 1)).payload_json)["query"].startswith("длинный")
        assert await get_context(fresh, view_id, 2) is None
    context.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    await session.commit()
    assert await get_context(session, view_id, 1) is None


async def test_search_and_habits_reach_last_page_with_short_callbacks(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    for i in range(31):
        await create(dao, text=f"Long task {i:02}")
    await session.commit()
    msg = message()
    await open_list(msg, dao, user, RU, kind="find", query="Long task")
    context = await session.scalar(select(UiContext).where(UiContext.kind == "list"))
    from bot.handlers.experience import render_list

    await render_list(msg, dao, user, RU, context, 999)
    assert "Long task 30" in msg.edit_text.await_args.args[0]
    assert all(
        len(b.callback_data.encode()) <= 64
        for row in msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    )
    for i in range(30):
        await create(dao, text=f"Habit {i:02}", habit=True, fluid=True)
    await session.commit()
    await open_list(msg, dao, user, RU, kind="habits")
    context = (
        await session.scalars(select(UiContext).where(UiContext.kind == "list").order_by(UiContext.expires_at.desc()))
    ).first()
    await render_list(msg, dao, user, RU, context, 999)
    assert "Habit 29" in msg.edit_text.await_args.args[0]


async def test_rename_preserves_schedule_tags_priority_and_return_page(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    task = await create(dao, habit=True)
    task.tags = "work"
    task.priority = 1
    await session.commit()
    before = (task.execution_time, task.rrule_string, task.rrule_dtstart, task.tags, task.priority)
    context = await create_context(session, 1, "list", {"kind": "all", "query": None})
    state = fsm()
    await state.update_data(rename_id=task.id, nav_token=context.token, nav_page=5)
    msg = message()
    msg.text = "Changed #literal"
    await rename_input(msg, state, dao, user, RU)
    assert task.reminder_text == "Changed #literal"
    assert (task.execution_time, task.rrule_string, task.rrule_dtstart, task.tags, task.priority) == before
    assert "Changed #literal" in msg.answer.await_args.args[0]


async def test_batch_freezes_members_and_is_idempotent_after_restart(database):
    session, maker = database
    user, dao, events = await user_dao(session)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    first = await create(dao, text="First", when=old)
    await session.commit()
    cb = callback("recovery_done_all")
    await begin_batch(cb, dao, user, RU)
    context = await session.scalar(select(UiContext).where(UiContext.kind == "batch"))
    second = await create(dao, text="New overdue", when=old)
    await session.commit()
    scheduler = Mock()
    apply = callback(f"batch:{context.token}:apply:0")
    await batch_action(apply, dao, events, scheduler, user, RU)
    assert first.status == "completed" and second.status == "pending"
    async with maker() as fresh:
        fresh_user = await fresh.get(User, 1)
        await batch_action(
            callback(f"batch:{context.token}:apply:0"),
            ReminderDAO(fresh),
            HabitEventDAO(fresh),
            scheduler,
            fresh_user,
            RU,
        )
        assert (await fresh.get(Reminder, second.id)).status == "pending"
    assert scheduler.remove_reminder_job.call_count == 1


async def test_batch_rejects_foreign_context_and_changed_cycle(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao, when=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3))
    await session.commit()
    await begin_batch(callback("recovery_done_all"), dao, user, RU)
    context = await session.scalar(select(UiContext).where(UiContext.kind == "batch"))
    foreign = await session.get(User, 2)
    await batch_action(callback(f"batch:{context.token}:apply:0"), dao, events, Mock(), foreign, RU)
    assert task.status == "pending"
    task.execution_time += timedelta(days=1)
    await session.commit()
    await batch_action(callback(f"batch:{context.token}:apply:0"), dao, events, Mock(), user, RU)
    assert task.status == "pending" and context.result == "0"


async def test_early_habit_completion_skips_notification_and_undo_restores_history(database):
    session, maker = database
    user, dao, events = await user_dao(session)
    task = await create(dao, habit=True)
    await session.commit()
    due = task.execution_time
    assert await set_daily_completion(dao, events, task, user, due)
    assert not await set_daily_completion(dao, events, task, user, due)
    assert task.habit_streak_current == 1
    scheduler = SchedulerService(AsyncIOScheduler(), Mock(), maker)
    scheduler.schedule_reminder = Mock()
    scheduler._send_or_replace_nag_message = AsyncMock()
    await scheduler._execute_reminder(task.id)
    scheduler._send_or_replace_nag_message.assert_not_awaited()
    await session.refresh(task)
    assert task.execution_time > due
    assert await set_daily_completion(dao, events, task, user, due, undo=True)
    assert task.habit_streak_current == 0
    assert not await events.has_event_for_cycle(task.id, f"due:{int(due.replace(tzinfo=timezone.utc).timestamp())}")


async def test_fluid_completion_and_undo_restore_previous_streak(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao, habit=True, fluid=True)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    task.fluid_last_completed_date = yesterday
    task.fluid_streak_current = 3
    task.fluid_streak_best = 3
    await session.commit()
    today = datetime.now(timezone.utc).date().isoformat()
    await set_daily_completion(dao, events, task, user, today)
    assert task.fluid_streak_current == 4
    await set_daily_completion(dao, events, task, user, today, undo=True)
    assert task.fluid_streak_current == 3 and task.fluid_last_completed_date == yesterday


async def test_habit_date_then_hour_preserves_start_and_disables_hidden_nagging(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    state = fsm()
    await state.set_state(HabitState.waiting_for_time)
    await state.update_data(habit_text="Read")
    day = (datetime.now(timezone.utc) + timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0)
    clock = datetime.now(timezone.utc).replace(hour=9, minute=30)
    with patch(
        "bot.handlers.habits.InputParser.parse",
        AsyncMock(
            side_effect=[
                SimpleNamespace(parsed_datetime=day, date_only=True, confidence=0.4),
                SimpleNamespace(parsed_datetime=clock, date_only=False, confidence=1),
            ]
        ),
    ):
        await state_habit_time(SimpleNamespace(text="Monday", answer=AsyncMock()), state, user, dao, Mock(), RU)
        assert (await session.scalars(select(Reminder))).all() == []
        await state_habit_time(SimpleNamespace(text="09:30", answer=AsyncMock()), state, user, dao, Mock(), RU)
    task = await session.scalar(select(Reminder))
    assert task.execution_time == day.replace(hour=9, minute=30, tzinfo=None)
    assert task.is_nagging is False


async def test_timezone_preview_defers_mutation_until_confirmation(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    from bot.database.dao.user import UserDAO

    user_settings = UserDAO(session)
    state = fsm()
    cb = callback("set_tz_UTC-03:30")
    await callback_set_tz(cb, user_settings, user, RU, state, dao)
    assert user.timezone == "UTC"
    await callback_timezone_apply(callback("timezone_apply"), state, user_settings, user, dao, RU)
    assert user.timezone == "UTC-03:30"
    assert resolve_timezone(user.timezone).localize(datetime(2026, 1, 1)).utcoffset() == timedelta(
        hours=-3, minutes=-30
    )
    assert resolve_timezone(user.timezone).localize(datetime(2026, 7, 1)).utcoffset() == timedelta(
        hours=-3, minutes=-30
    )
    assert _resolve_timezone_candidate("Ташкент") == "Asia/Tashkent"


async def test_notification_preset_is_explicit_atomic_and_keeps_quiet_hours(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    user.quiet_hours_enabled = True
    context = await create_context(session, 1, "notify", {"choice": "only"})
    assert user.briefs_enabled
    await apply_notifications(callback(f"notifyapply:{context.token}"), dao, user, RU)
    assert not user.briefs_enabled and not user.habit_reports_enabled and not user.missed_recovery_enabled
    assert user.quiet_hours_enabled
    user.briefs_enabled = True
    await session.commit()
    await apply_notifications(callback(f"notifyapply:{context.token}"), dao, user, RU)
    assert user.briefs_enabled  # Replaying an applied token does not overwrite later edits.


async def test_reset_erases_contexts_with_old_queries_and_batches(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    await create_context(session, 1, "list", {"kind": "find", "query": "private query"})
    await UserDataService(dao, events).reset(user)
    assert (await session.scalars(select(UiContext).where(UiContext.user_id == 1))).all() == []


def test_fixed_offset_recurrence_preserves_wall_clock():
    zone = "UTC+05:45"
    due = datetime(2026, 3, 28, 3, 15)
    nxt = next_occurrence_utc("FREQ=DAILY", due, zone, due)
    assert nxt == due + timedelta(days=1)


async def test_recurring_schedule_needs_confirmation_and_reanchors_only_after_it(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    task = await create(dao, habit=True)
    task.rrule_string = "FREQ=DAILY;COUNT=3"
    await session.commit()
    original = task.execution_time
    anchor = task.rrule_dtstart
    changed = (original + timedelta(hours=2)).replace(microsecond=0)
    state = fsm()
    await state.update_data(edit_reminder_id=task.id, text=task.reminder_text, execution_time=changed.isoformat())
    from bot.handlers.reminders_shared import _save_and_show_edit
    from bot.handlers.reminders_wizard import callback_series_confirm

    scheduler = Mock()
    msg = message()
    await _save_and_show_edit(msg, state, RU, user, dao, scheduler)
    assert task.execution_time == original and task.rrule_dtstart == anchor
    scheduler.schedule_reminder.assert_not_called()
    assert (await state.get_state()) == "ReminderWizard:confirming_schedule"
    assert RU["series_restart_count"] in msg.answer.await_args.args[0]
    await callback_series_confirm(
        callback("series_confirm:" + (await state.get_data())["series_nonce"], msg), state, user, dao, scheduler, RU
    )
    assert task.execution_time == changed and task.rrule_dtstart == changed


async def test_snoozing_only_current_occurrence_keeps_series_anchor(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    task = await create(dao, habit=True)
    await session.commit()
    original = (task.execution_time, task.rrule_dtstart)
    target = task.execution_time + timedelta(minutes=30)
    state = fsm()
    await state.update_data(
        edit_reminder_id=task.id,
        text=task.reminder_text,
        scope_candidate={"time": target.isoformat(), "date_only": False, "confidence": 1},
    )
    from bot.handlers.reminders_wizard import callback_edit_scope

    scheduler = Mock()
    from bot.handlers.reminders_wizard import _show_scope

    await _show_scope(message(), state, task, RU)
    await callback_edit_scope(
        callback("edit_scope:" + (await state.get_data())["scope_nonce"] + ":one"), state, user, dao, scheduler, RU
    )
    assert (task.execution_time, task.rrule_dtstart) == original
    assert scheduler.schedule_reminder.call_args.args[1].replace(tzinfo=None) == target


async def test_series_edit_beyond_until_does_not_save(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    task = await create(dao, habit=True)
    task.rrule_string = "FREQ=DAILY;UNTIL=" + (task.execution_time + timedelta(days=1)).strftime("%Y%m%dT%H%M%S")
    await session.commit()
    original = task.execution_time
    state = fsm()
    await state.update_data(
        edit_reminder_id=task.id, text=task.reminder_text, execution_time=(original + timedelta(days=3)).isoformat()
    )
    from bot.handlers.reminders_shared import _save_and_show_edit

    scheduler = Mock()
    msg = message()
    await _save_and_show_edit(msg, state, RU, user, dao, scheduler)
    assert task.execution_time == original
    assert msg.answer.await_args.args[0] == RU["series_expired"]
    scheduler.schedule_reminder.assert_not_called()


async def test_uncertain_habit_confirmation_is_consumed_once(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    from bot.handlers.habits import confirm_habit_time

    state = fsm()
    await state.update_data(habit_text="Read")
    time = datetime.now(timezone.utc) + timedelta(days=2)
    with patch(
        "bot.handlers.habits.InputParser.parse",
        AsyncMock(return_value=SimpleNamespace(parsed_datetime=time, date_only=False, confidence=0.5)),
    ):
        await state_habit_time(SimpleNamespace(text="uncertain", answer=AsyncMock()), state, user, dao, Mock(), RU)
    assert (await session.scalars(select(Reminder))).all() == []
    await confirm_habit_time(
        callback("habit_time_confirm:" + (await state.get_data()).get("habit_confirm_token", "expired")),
        state,
        user,
        dao,
        Mock(),
        RU,
    )
    await confirm_habit_time(
        callback("habit_time_confirm:" + (await state.get_data()).get("habit_confirm_token", "expired")),
        state,
        user,
        dao,
        Mock(),
        RU,
    )
    assert len((await session.scalars(select(Reminder))).all()) == 1


async def test_batch_commit_failure_preserves_tasks_and_confirmation(database):
    session, maker = database
    user, dao, events = await user_dao(session)
    task = await create(dao, when=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2))
    await session.commit()
    task_id = task.id
    await begin_batch(callback("recovery_done_all"), dao, user, RU)
    ctx = await session.scalar(select(UiContext).where(UiContext.kind == "batch"))
    token = ctx.token
    original_commit = session.commit
    session.commit = AsyncMock(side_effect=RuntimeError("injected storage failure"))
    scheduler = Mock()
    try:
        await batch_action(callback(f"batch:{token}:apply:0"), dao, events, scheduler, user, RU)
    finally:
        session.commit = original_commit
    async with maker() as fresh:
        assert (await fresh.get(Reminder, task_id)).status == "pending"
        assert (await fresh.get(UiContext, token)).result is None
    scheduler.remove_reminder_job.assert_not_called()


async def test_notification_offer_does_not_change_settings_or_repeat(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    await create(dao)
    await session.commit()
    from bot.middlewares.notification_offer import NotificationOfferMiddleware

    msg = message()
    event = SimpleNamespace(message=msg)
    data = {"reminder_dao": dao, "user": user, "l10n": RU}
    middleware = NotificationOfferMiddleware()
    await middleware(AsyncMock(), event, data)
    await middleware(AsyncMock(), event, data)
    msg.answer.assert_awaited_once()
    assert user.briefs_enabled and user.missed_recovery_enabled and user.habit_reports_enabled


async def test_fixed_zone_calendar_embeds_offset_definition(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    user.timezone = "UTC-03:30"
    task = await create(dao, habit=True)
    from bot.services.ics_feed import build_ics_calendar

    calendar = build_ics_calendar(user, [task])
    assert "BEGIN:VTIMEZONE" in calendar and "TZOFFSETTO:-0330" in calendar
    assert 'DTSTART;TZID="UTC-03:30":' in calendar


async def test_manual_task_hour_inherits_recognized_day(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    from bot.handlers.reminders_shared import _resolve_time_and_respond

    state = fsm()
    await state.update_data(text="Read")
    day = (datetime.now(timezone.utc) + timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0)
    msg = message()
    msg.text = "Monday"
    await _resolve_time_and_respond(
        msg, state, user, RU, SimpleNamespace(parsed_datetime=day, date_only=True, confidence=0.4), dao, Mock(), "Read"
    )
    msg.text = "09:30"
    clock = datetime.now(timezone.utc).replace(hour=9, minute=30, second=0, microsecond=0)
    await _resolve_time_and_respond(
        msg, state, user, RU, SimpleNamespace(parsed_datetime=clock, date_only=False, confidence=1), dao, Mock(), "Read"
    )
    task = await session.scalar(select(Reminder))
    assert task.execution_time == day.replace(hour=9, minute=30, tzinfo=None)


async def test_daily_action_accepts_stored_fractional_seconds_and_clears_other_input(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao, habit=True)
    task.execution_time = task.execution_time.replace(microsecond=123456)
    await session.commit()
    ctx = await create_context(session, 1, "list", {"kind": "habits", "query": None})
    state = fsm()
    await state.set_state("ExperienceState:renaming")
    await state.update_data(rename_id=task.id)
    from bot.handlers.experience import task_action

    seconds = int(task.execution_time.replace(tzinfo=timezone.utc).timestamp())
    await task_action(callback(f"ux:{ctx.token}:0:habitdone:{task.id}:{seconds}"), state, dao, events, Mock(), user, RU)
    assert await events.has_event_for_cycle(task.id, f"due:{seconds}")
    assert task.completed_for_execution_time == task.execution_time
    assert await state.get_state() is None


async def test_batch_does_not_complete_a_task_snoozed_after_preview(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao, when=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3))
    await session.commit()
    await begin_batch(callback("recovery_done_all"), dao, user, RU)
    ctx = await session.scalar(select(UiContext).where(UiContext.kind == "batch"))
    task.snooze_count = 1
    await session.commit()
    await batch_action(callback(f"batch:{ctx.token}:apply:0"), dao, events, Mock(), user, RU)
    assert task.status == "pending" and ctx.result == "0"


async def test_concurrent_daily_checkins_record_one_event(database):
    import asyncio

    session, maker = database
    _, dao, _ = await user_dao(session)
    task = await create(dao, habit=True)
    await session.commit()
    rid, due = task.id, task.execution_time

    async def mark():
        async with maker() as fresh:
            user = await fresh.get(User, 1)
            current = await fresh.get(Reminder, rid)
            return await set_daily_completion(ReminderDAO(fresh), HabitEventDAO(fresh), current, user, due)

    assert sorted(await asyncio.gather(mark(), mark())) == [False, True]
    await session.refresh(task)
    assert task.habit_streak_current == 1
    assert len((await session.scalars(select(HabitEvent).where(HabitEvent.reminder_id == rid))).all()) == 1


async def test_old_plain_task_done_button_cannot_close_a_new_occurrence(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao)
    await session.commit()
    ctx = await create_context(session, 1, "list", {"kind": "all", "query": None})
    seconds = int(task.execution_time.replace(tzinfo=timezone.utc).timestamp())
    task.execution_time += timedelta(days=1)
    await session.commit()
    from bot.handlers.experience import task_action

    await task_action(callback(f"ux:{ctx.token}:0:done:{task.id}:{seconds}"), fsm(), dao, events, Mock(), user, RU)
    assert task.status == "pending"


async def test_old_habit_confirm_cannot_confirm_a_new_candidate(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    state = fsm()
    await state.update_data(habit_text="Read")
    parsed = SimpleNamespace(
        parsed_datetime=datetime.now(timezone.utc) + timedelta(days=2), date_only=False, confidence=0.5
    )
    with patch("bot.handlers.habits.InputParser.parse", AsyncMock(return_value=parsed)):
        await state_habit_time(SimpleNamespace(text="uncertain", answer=AsyncMock()), state, user, dao, Mock(), RU)
        first_confirmation = (await state.get_data())["habit_confirm_token"]
        await state_habit_time(
            SimpleNamespace(text="another uncertain", answer=AsyncMock()), state, user, dao, Mock(), RU
        )
    from bot.handlers.habits import confirm_habit_time

    await confirm_habit_time(callback("habit_time_confirm:" + first_confirmation), state, user, dao, Mock(), RU)
    assert (await session.scalars(select(Reminder))).all() == []


async def test_username_bootstrap_releases_sqlite_lock_before_fsm(database):
    session, maker = database
    from aiogram.types import User as TelegramUser

    from bot.middlewares.database import DatabaseMiddleware
    from bot.services.fsm_storage import SQLAlchemyFSMStorage

    state = FSMContext(SQLAlchemyFSMStorage(maker), StorageKey(bot_id=1, chat_id=1, user_id=1))

    async def handler(event, data):
        await state.update_data(flow="test")

    await DatabaseMiddleware(maker)(
        handler,
        SimpleNamespace(),
        {"event_from_user": TelegramUser(id=1, is_bot=False, first_name="Test", username="changed_name")},
    )
    assert (await state.get_data())["flow"] == "test"


async def test_long_unicode_tag_is_addressable_without_raw_callback_text(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    tag = "测" * 24
    task = await create(dao, text="Tagged task")
    task.tags = tag
    await session.commit()
    from bot.handlers.tag_navigation import open_tags, tag_action

    cb = callback("tasks_tags_menu")
    await open_tags(cb, dao, user, RU)
    markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
    data = markup.inline_keyboard[0][0].callback_data
    assert tag not in data and len(data.encode()) <= 64
    await tag_action(callback(data, cb.message), dao, user, RU)
    assert "Tagged task" in cb.message.edit_text.await_args.args[0]


async def test_habit_details_keep_progress_and_hide_fluid_placeholder_date(database):
    session, _ = database
    user, dao, events = await user_dao(session)
    task = await create(dao, habit=True, fluid=True, when=datetime(2035, 1, 1))
    await session.commit()
    await set_daily_completion(dao, events, task, user, datetime.now(timezone.utc).date().isoformat())
    ctx = await create_context(session, 1, "list", {"kind": "habits", "query": None})
    from bot.handlers.experience import task_action

    cb = callback(f"ux:{ctx.token}:0:detail:{task.id}")
    await task_action(cb, fsm(), dao, events, Mock(), user, RU)
    text = cb.message.edit_text.await_args.args[0]
    assert "20/100" in text and "2035" not in text


async def test_upgrade_adds_ui_contexts_without_changing_saved_tasks(database):
    session, _ = database
    user, dao, _ = await user_dao(session)
    user.timezone = "America/St_Johns"
    task = await create(dao, text="Existing reminder", habit=True)
    task.is_nagging = True
    await session.commit()
    original_time = task.execution_time
    engine = session.bind
    # The preceding release has all domain tables but no UI context table.
    async with engine.begin() as connection:
        await connection.run_sync(UiContext.__table__.drop)
    await init_db(engine)
    await init_db(engine)
    await session.refresh(user)
    await session.refresh(task)
    assert user.timezone == "America/St_Johns"
    assert task.reminder_text == "Existing reminder"
    assert task.execution_time == original_time
    assert task.is_nagging is True
    context = await create_context(session, user.id, "list", {"kind": "all"})
    assert await get_context(session, context.token, user.id, "list") is not None
