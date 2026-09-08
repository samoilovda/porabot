from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.lexicon import get_l10n
from bot.services.habit_reports import _aggregate, _process_user_reports


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_user_and_habit(session) -> tuple[User, "models.Reminder"]:
    user = User(id=1, username="u", timezone="UTC")
    session.add(user)
    await session.flush()
    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Workout",
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    return user, reminder


def test_weekly_window_is_seven_local_dates_inclusive() -> None:
    today = date(2026, 5, 10)
    start = today - timedelta(days=6)
    assert (today - start).days == 6
    assert start == date(2026, 5, 4)


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 5, 31), True),   # last Sunday of a 5-Sunday May 2026
        (date(2026, 5, 24), False),  # penultimate Sunday — no monthly report yet
    ],
)
def test_last_weekday_of_month_detection(today: date, expected: bool) -> None:
    is_last = (today + timedelta(days=7)).month != today.month
    assert is_last is expected


def test_monthly_window_is_first_of_month_to_send_day() -> None:
    today = date(2026, 5, 31)
    month_start = today.replace(day=1)
    assert month_start == date(2026, 5, 1)


async def test_no_events_in_window_sends_nothing(session) -> None:
    user, reminder = await _make_user_and_habit(session)
    l10n = get_l10n("en")
    sent = []

    class FakeBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    await _process_user_reports(session, FakeBot(), user, datetime(2026, 5, 10, 23, 50))
    assert sent == []


def test_aggregate_sums_not_today_and_missed_together() -> None:
    events = [
        _fake_event(reminder_id=1, outcome="done", local_date="2026-05-01", habit_text="Workout"),
        _fake_event(reminder_id=1, outcome="not_today", local_date="2026-05-02", habit_text="Workout"),
        _fake_event(reminder_id=1, outcome="missed", local_date="2026-05-03", habit_text="Workout"),
    ]
    rows = _aggregate(events)
    assert len(rows) == 1
    assert rows[0]["done"] == 1
    assert rows[0]["not_today"] == 2
    assert rows[0]["total"] == 3


def _fake_event(*, reminder_id, outcome, local_date, habit_text):
    from types import SimpleNamespace

    return SimpleNamespace(reminder_id=reminder_id, outcome=outcome, local_date=local_date, habit_text=habit_text)
