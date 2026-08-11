"""Handlers package — collects all routers for registration."""

from aiogram import Router

from bot.handlers.commands import router as commands_router
from bot.handlers.donate import router as donate_router
from bot.handlers.menu import router as menu_router
from bot.handlers.reminders import router as reminders_router
from bot.handlers.settings import router as settings_router
from bot.handlers.habits import router as habits_router
from bot.handlers.admin import router as admin_router

# Order matters: more specific routers first, catch-all last.
# menu_router must come before any router with stateful FSM handlers that
# lack a text filter (settings/habits/reminders all have some), otherwise a
# menu button tap while mid-wizard in another flow gets swallowed by that
# flow's state handler instead of reaching the menu button handler.
# donate_router has no catch-all handlers (Command("donate"), specific
# callback_data prefixes, F.successful_payment) so its position relative
# to the others doesn't matter beyond staying ahead of reminders_router.
all_routers: list[Router] = [
    admin_router,
    commands_router,
    donate_router,
    menu_router,
    settings_router,
    habits_router,
    reminders_router,  # contains the catch-all text handler — must be last
]
