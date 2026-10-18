"""Shared database contract for existing handler tests using small fake DAOs.

The navigation layer now persists contexts. Keep these routing/rendering tests
isolated from application data while providing a real context database; the
experience integration suite checks persistence against real DAO data.
"""

from types import SimpleNamespace as Namespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base


@pytest.fixture(autouse=True)
async def navigation_dao_contract(request, monkeypatch):
    modules = {"test_menu_router_priority", "test_tasks_list_pagination", "test_task_search_and_filters"}
    if request.module.__name__.split(".")[-1] not in modules:
        yield
        return
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:

        def namespace(**kwargs):
            if "clear" in kwargs:
                kwargs.setdefault("get_state", AsyncMock(return_value=None))
            if any(
                k.startswith(("get_user_reminders", "search_user_reminders", "get_habit_motivation")) for k in kwargs
            ):
                kwargs.setdefault("session", session)
                for method in (
                    "get_user_reminders",
                    "get_user_reminders_today",
                    "get_active_habits",
                    "get_active_fluid_habits",
                    "get_overdue_pending_tasks",
                ):
                    kwargs.setdefault(method, AsyncMock(return_value=[]))
            if "reminder_text" in kwargs:
                for key, value in {
                    "is_habit": False,
                    "is_fluid_habit": False,
                    "paused_until": None,
                    "habit_active_due_at": None,
                    "habit_last_completed_due_at": None,
                    "fluid_last_completed_date": None,
                }.items():
                    kwargs.setdefault(key, value)
            return Namespace(**kwargs)

        monkeypatch.setattr(request.module, "SimpleNamespace", namespace)
        yield
    await engine.dispose()
