"""Audit 2026-11-18 #10: every list entry gets its own button row — the primary
action (with number and a short name) followed by Edit — instead of a flat
2-column grid in which an entry without a primary action shifts the next
entry's buttons into its row.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import create_engine, create_session_maker, init_db
from bot.database.models import User
from bot.handlers.experience import _button_name, open_list
from bot.lexicon import get_l10n

L10N = get_l10n("en")


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/rows.sqlite")
    await init_db(engine)
    maker = create_session_maker(engine)
    async with maker() as session:
        session.add(User(id=1, timezone="UTC", language="en"))
        await session.commit()
        yield session
    await engine.dispose()


def _msg():
    return SimpleNamespace(
        text="List", chat=SimpleNamespace(id=1), message_id=1,
        answer=AsyncMock(), edit_text=AsyncMock(), edit_reply_markup=AsyncMock(),
    )


async def _fluid(dao, text, paused=False):
    h = await dao.create_reminder(
        user_id=1, text=text, execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2),
        is_recurring=False, is_habit=True, is_fluid_habit=True,
    )
    if paused:
        h.paused_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3)
    return h


def _rows(msg):
    call = msg.edit_text.await_args or msg.answer.await_args
    kb = call.kwargs["reply_markup"]
    return [[b.text for b in row] for row in kb.inline_keyboard]


async def test_each_entry_has_its_own_row_with_mixed_active_and_paused(database) -> None:
    session = database
    user, dao = await session.get(User, 1), ReminderDAO(session)
    await _fluid(dao, "Morning run along the river for an hour")
    await _fluid(dao, "Paused meditation", paused=True)
    await _fluid(dao, "Read")
    await session.commit()

    msg = _msg()
    await open_list(msg, dao, user, L10N, kind="habits")
    rows = _rows(msg)

    entry_rows = [r for r in rows if any(t.endswith(("1", "2", "3")) and L10N["btn_edit"] in t for t in r)]
    assert len(entry_rows) == 3
    # Order of the list is by schedule; locate each entry by its Edit button number.
    by_number = {next(t for t in r if t.startswith(L10N["btn_edit"])).split()[-1]: r for r in entry_rows}
    assert set(by_number) == {"1", "2", "3"}
    for number, row in by_number.items():
        assert row[-1] == f"{L10N['btn_edit']} {number}"
        assert len(row) in (1, 2)
    paused_rows = [r for r in entry_rows if len(r) == 1]
    active_rows = [r for r in entry_rows if len(r) == 2]
    assert len(paused_rows) == 1 and len(active_rows) == 2  # the paused one has only Edit
    for row in active_rows:
        assert row[0].startswith("✅ ")  # primary action first, with number and short name


async def test_long_name_is_truncated_in_button(database) -> None:
    session = database
    user, dao = await session.get(User, 1), ReminderDAO(session)
    await _fluid(dao, "Morning run along the river for an hour")
    await session.commit()

    msg = _msg()
    await open_list(msg, dao, user, L10N, kind="habits")
    primary = next(t for row in _rows(msg) for t in row if t.startswith("✅"))

    assert primary.endswith("…")
    assert primary.split(" ", 2)[2] == _button_name("Morning run along the river for an hour")
    assert len(primary.split(" ", 2)[2]) <= 20


def test_button_name_helper() -> None:
    assert _button_name("Read") == "Read"
    assert _button_name("  multi\nline  text ") == "multi line text"
    exactly = "x" * 20
    assert _button_name(exactly) == exactly
    assert _button_name("x" * 21) == "x" * 19 + "…"
    assert _button_name("") == ""


async def test_plain_tasks_get_rows_too(database) -> None:
    session = database
    user, dao = await session.get(User, 1), ReminderDAO(session)
    for i in range(3):
        await dao.create_reminder(
            user_id=1, text=f"Task number {i}", execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2 + i),
        )
    await session.commit()

    msg = _msg()
    await open_list(msg, dao, user, L10N, kind="all")
    entry_rows = [r for r in _rows(msg) if r[0].startswith("✅")]
    assert len(entry_rows) == 3
    assert all(len(r) == 2 and r[1].startswith(L10N["btn_edit"]) for r in entry_rows)
