"""Reply keyboards (persistent bottom menu)."""

from typing import Any
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_main_menu_keyboard(l10n: dict[str, Any]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=l10n["btn_new_task"]),
        KeyboardButton(text=l10n["btn_my_tasks"]),
    )
    builder.row(KeyboardButton(text=l10n["btn_settings"]))
    return builder.as_markup(resize_keyboard=True)
