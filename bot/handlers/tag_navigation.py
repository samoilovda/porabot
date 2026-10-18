"""Paginated tag selection without embedding tag text in callback data."""

import json
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.keyboards.inline import callback_button as _button
from bot.services.ui_context import create_context, get_context

router = Router(name="tag_navigation")


@router.callback_query(F.data == "tasks_tags_menu")
async def open_tags(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]) -> None:
    tags = await reminder_dao.get_distinct_tags(user.id)
    if not tags:
        return await callback.answer(l10n["no_tags_yet"], show_alert=True)
    context = await create_context(reminder_dao.session, user.id, "tags", {"tags": list(tags)})
    await render_tags(callback.message, context, l10n, 0)
    await callback.answer()


async def render_tags(message, context, l10n, page):
    tags = json.loads(context.payload_json)["tags"]
    pages = max(1, (len(tags) + 19) // 20)
    page = max(0, min(page, pages - 1))
    builder = InlineKeyboardBuilder()
    for i, tag in enumerate(tags[page * 20 : page * 20 + 20], start=page * 20):
        builder.row(_button("#" + tag[:100], f"tagselect:{context.token}:{i}"))
    if page > 0:
        builder.row(_button("◀", f"tagpage:{context.token}:{page - 1}"))
    if page + 1 < pages:
        builder.row(_button("▶", f"tagpage:{context.token}:{page + 1}"))
    builder.row(_button(l10n["btn_filter_back"], "uxlist:all"))
    await message.edit_text(
        l10n["tags_menu_title"] + f" {page + 1}/{pages}", reply_markup=builder.as_markup(), parse_mode=None
    )


@router.callback_query(F.data.startswith("tagpage:") | F.data.startswith("tagselect:"))
async def tag_action(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]) -> None:
    from bot.handlers.experience import open_list

    try:
        action, token, raw = callback.data.split(":")
        index = int(raw)
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    context = await get_context(reminder_dao.session, token, user.id, "tags")
    if not context:
        return await callback.answer(l10n["context_expired"], show_alert=True)
    if action == "tagpage":
        await render_tags(callback.message, context, l10n, index)
    else:
        tags = json.loads(context.payload_json)["tags"]
        if not 0 <= index < len(tags):
            return await callback.answer(l10n["invalid_action"], show_alert=True)
        await open_list(callback.message, reminder_dao, user, l10n, kind="tag", query=tags[index], edit=True)
    await callback.answer()
