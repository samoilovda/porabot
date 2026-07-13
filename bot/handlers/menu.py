"""Main menu button handlers — registered on their own router, early in the
router chain (right after commands, before settings/habits/reminders).

Why a dedicated router: several other routers have FSM-state-scoped message
handlers with no text filter (e.g. waiting_for_brief_time in settings.py,
waiting_for_name in habits.py). Those routers used to also own one of the
menu buttons, so whichever router ran first for a given update could swallow
a menu button tap meant for a LATER router — e.g. a user mid-way through
setting a brief time who taps "My Tasks" would have that text swallowed by
the brief-time validator instead of reaching reminders.py. Registering all
four menu buttons on one router placed before every FSM-state router fixes
that regardless of what state the user happens to be in.
"""

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.habits import _habit_motivation_text, get_habits_keyboard
from bot.handlers.reminders import _format_task_line_md2, _paginate_tasks_for_list
from bot.handlers.settings import _render_settings_text
from bot.keyboards.inline import get_settings_keyboard, get_tasks_list_keyboard
from bot.keyboards.reply import get_main_menu_keyboard
from bot.states.reminder import ReminderWizard

router = Router(name="menu")


@router.message(F.text.in_(["➕ Новая задача", "➕ New Task", "➕ Nueva tarea"]))
async def btn_new_task(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await state.set_state(ReminderWizard.entering_text)
    await message.answer(l10n["enter_task"], parse_mode="Markdown")


@router.message(F.text.in_(["📅 Мои задачи", "📅 My Tasks", "📅 Mis tareas"]))
async def btn_my_tasks(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    await state.clear()
    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await message.answer(l10n["no_tasks"], reply_markup=get_main_menu_keyboard(l10n))
        return

    shown_tasks, overflow_suffix = _paginate_tasks_for_list(tasks, l10n)
    lines = [l10n["tasks_header"]] + [_format_task_line_md2(task, user) for task in shown_tasks]
    if overflow_suffix:
        lines.append(overflow_suffix)

    safe_text = "\n".join(lines)
    await message.answer(safe_text, reply_markup=get_tasks_list_keyboard(shown_tasks, l10n), parse_mode="MarkdownV2")


@router.message(F.text.in_(["⚙️ Настройки", "⚙️ Settings", "⚙️ Ajustes"]))
async def btn_settings(message: Message, state: FSMContext, user: User, l10n: dict[str, Any]) -> None:
    await state.clear()  # Reset FSM if user navigates here mid-wizard
    text = _render_settings_text(user, l10n)
    await message.answer(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")


@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits", "🫧 Hábitos"]))
async def btn_habits(
    message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    user: User,
) -> None:
    """Show the habits dashboard."""
    await state.clear()
    stats = await reminder_dao.get_habit_motivation_stats(user.id, user.timezone, days=7)
    motivation = _habit_motivation_text(l10n, stats)
    text = l10n["habits_dashboard"]
    if motivation.strip():
        text = f"{text}\n\n{motivation}"
    await message.answer(
        text,
        reply_markup=get_habits_keyboard(l10n).as_markup(),
        parse_mode="Markdown",
    )
