"""Handlers package — collects all routers for registration."""

from aiogram import Router

from bot.handlers.commands import router as commands_router
from bot.handlers.reminders import router as reminders_router
from bot.handlers.settings import router as settings_router
from bot.handlers.habits import router as habits_router

# Order matters: more specific routers first, catch-all last
all_routers: list[Router] = [
    commands_router,
    settings_router,
    habits_router,
    reminders_router,  # contains the catch-all text handler — must be last
]
