# В этом файле нужно обновить импорты, чтобы использовать scheduler из loader.py или scheduler.py (функции-обертки)
# Импортируем функции управления из scheduler.py, а не сам инстанс scheduler'а,
# чтобы соблюсти инкапсуляцию, которую мы ввели.

import logging
from datetime import datetime, timedelta
import pytz

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Reminder
from services import InputParser
from keyboards import (
    get_main_menu_keyboard,
    get_time_selection_keyboard,
    get_confirmation_keyboard,
    get_timezone_keyboard,
    get_task_done_keyboard,
    get_tasks_list_keyboard,
    get_settings_keyboard
)

# Используем функции-хелперы из переписанного scheduler.py
from scheduler import schedule_reminder, remove_reminder_job, remove_nagging_job

router = Router()
parser = InputParser()
logger = logging.getLogger(__name__)

class ReminderWizard(StatesGroup):
    entering_text = State() 
    choosing_time = State() 
    confirming = State()     

# ... Весь остальной код остается похожим, но с заменой прямых вызовов scheduler ...

# --- Команды и Главное Меню ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User):
    await state.clear()
    text = (
        f"👋 Привет, {message.from_user.first_name}!\n"
        "Я **Porabot**. Я помогу тебе не прокрастинировать.\n\n"
        "Выбери действие в меню 👇"
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard())

# --- Обработка кнопок Главного Меню ---

@router.message(F.text == "➕ Новая задача")
async def btn_new_task(message: Message, state: FSMContext):
    await state.set_state(ReminderWizard.entering_text)
    await message.answer(
        "Напиши, о чем напомнить.\n"
        "Можно сразу с временем: *\"Позвонить маме завтра в 18:00\"*.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

@router.message(F.text == "📅 Мои задачи")
async def btn_my_tasks(message: Message, session: AsyncSession, user: User):
    q = select(Reminder).where(Reminder.user_id == user.id).order_by(Reminder.execution_time)
    result = await session.execute(q)
    tasks = result.scalars().all()
    
    if not tasks:
        await message.answer("🎉 У тебя нет активных задач. Отдыхай!", reply_markup=get_main_menu_keyboard())
        return
    
    text_lines = ["📋 **Твои задачи:**\n"]
    for task in tasks:
        # User timezone handling for display?
        # Задача хранится в naive UTC (или как распарсилось).
        # Лучше приводить к таймзоне юзера для отображения.
        
        dt_display = task.execution_time
        try:
            if user.timezone:
                user_tz = pytz.timezone(user.timezone)
                if dt_display.tzinfo:
                    dt_display = dt_display.astimezone(user_tz)
                else:
                    # Если naive, считаем что это в таймзоне юзера?
                    # Нет, мы сохраняли isoformat().
                    # Assume naive is compatible or localized already.
                    pass
        except Exception:
            pass # Fallback
            
        dt_str = dt_display.strftime("%d.%m %H:%M")
        recur_icon = "🔁 " if task.is_recurring else ""
        nag_icon = "🔥 " if task.is_nagging else ""
        text_lines.append(f"▫️ `{dt_str}`: {recur_icon}{nag_icon}{task.reminder_text}")
        
    final_text = "\n".join(text_lines)
    await message.answer(final_text, reply_markup=get_tasks_list_keyboard(tasks), parse_mode="Markdown")

@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message, user: User):
    text = (
        f"⚙️ **Настройки**\n\n"
        f"🌍 Твой часовой пояс: `{user.timezone}`\n\n"
        "Если время напоминаний скачет, проверь пояс."
    )
    await message.answer(text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")


# --- Wizard ---

@router.message(ReminderWizard.entering_text, F.text)
@router.message(F.text) 
async def handle_task_text(message: Message, state: FSMContext, user: User):
    if message.text in ["➕ Новая задача", "📅 Мои задачи", "⚙️ Настройки"]:
        return

    try:
        # Теперь метод асинхронный!
        result = await parser.parse_input(message.text, user.timezone)
        clean_text = result['clean_text'] or "Без названия"
        parsed_dt = result['parsed_datetime']

        await state.update_data(
            text=clean_text,
            user_timezone=user.timezone,
            chat_id=message.chat.id
        )

        if parsed_dt:
            await state.update_data(execution_time=parsed_dt.isoformat())
            await state.set_state(ReminderWizard.confirming)
            await show_confirmation(message, state)
        else:
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(
                f"Ок, задача: \"{clean_text}\".\nКогда напомнить?",
                reply_markup=get_time_selection_keyboard(user.timezone)
            )
    except Exception as e:
        logger.error(f"Error parsing text: {e}")
        await message.answer("Ошибка обработки текста. Проверь формат.")


@router.callback_query(ReminderWizard.choosing_time, F.data.startswith("time_"))
async def callback_time_selected(callback: CallbackQuery, state: FSMContext, user: User):
    data_str = callback.data
    try:
        tz = pytz.timezone(user.timezone)
    except Exception:
        tz = pytz.UTC
        
    now = datetime.now(tz)
    execution_time = None

    if "delta" in data_str:
        minutes = int(data_str.split("_")[-1])
        execution_time = now + timedelta(minutes=minutes)
    elif "fixed" in data_str:
        iso_str = data_str.split("_fixed_")[1]
        execution_time = datetime.fromisoformat(iso_str)
    elif "tomorrow" in data_str:
        # Завтра 09:00
        execution_time = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif "manual" in data_str:
        await callback.message.edit_text("Попробуй снова написать задачу ЦЕЛИКОМ, включая дату и время.")
        return

    if execution_time:
        await state.update_data(execution_time=execution_time.isoformat())
        await state.set_state(ReminderWizard.confirming)
        await callback.message.delete()
        await show_confirmation(callback.message, state)


async def show_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text")
    exec_time_iso = data.get("execution_time")
    
    is_recurring = data.get("is_recurring", False)
    is_nagging = data.get("is_nagging", False)
    rrule_option = data.get("rrule_option", "Нет")
    
    dt = datetime.fromisoformat(exec_time_iso)
    date_str = dt.strftime("%d.%m.%Y %H:%M")
    
    preview = (
        f"📌 **Задача:** {text}\n"
        f"⏰ **Время:** {date_str}\n" 
        f"----------------------\n"
        f"Нажми 'ПОРА!', чтобы сохранить."
    )
    
    keyboard = get_confirmation_keyboard(is_recurring, is_nagging, rrule_option)
    await message.answer(preview, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(ReminderWizard.confirming, F.data.startswith("conf_"))
async def callback_confirm_action(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    action = callback.data.split("conf_")[1]
    data = await state.get_data()
    
    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Отменено.", reply_markup=None)
        # Restore menu msg?
        await callback.message.answer("Главное меню", reply_markup=get_main_menu_keyboard())
        return

    if action == "save":
        text = data.get("text")
        exec_time_iso = data.get("execution_time")
        rrule_option = data.get("rrule_option", "Нет")
        is_nagging = data.get("is_nagging", False)
        user_id = callback.from_user.id
        
        rrule_str = None
        is_recurring = False
        if rrule_option == "День":
            is_recurring = True
            rrule_str = "FREQ=DAILY"
        elif rrule_option == "Неделя":
            is_recurring = True
            rrule_str = "FREQ=WEEKLY"
        elif rrule_option == "Будни":
            is_recurring = True
            rrule_str = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
            
        new_reminder = Reminder(
            user_id=user_id,
            reminder_text=text,
            execution_time=datetime.fromisoformat(exec_time_iso),
            is_recurring=is_recurring,
            rrule_string=rrule_str,
            is_nagging=is_nagging
        )
        session.add(new_reminder)
        await session.commit()
        await session.refresh(new_reminder)
        
        # Используем функцию из scheduler.py
        schedule_reminder(new_reminder.id, new_reminder.execution_time, new_reminder.is_nagging)
        
        await state.clear()
        await callback.message.edit_text(
            f"✅ **Задача сохранена!**", 
            parse_mode="Markdown",
            reply_markup=None
        )
        await callback.message.answer("Что дальше?", reply_markup=get_main_menu_keyboard())
        return

    # Toggles
    if action == "toggle_repeat":
        options = ["Нет", "День", "Будни", "Неделя"]
        current = data.get("rrule_option", "Нет")
        try:
            current_idx = options.index(current)
            next_idx = (current_idx + 1) % len(options)
        except ValueError:
            next_idx = 0
        await state.update_data(rrule_option=options[next_idx])
        
    elif action == "toggle_nagging":
        current = data.get("is_nagging", False)
        await state.update_data(is_nagging=not current)
        
    # Re-render
    data = await state.get_data()
    keyboard = get_confirmation_keyboard(
        is_recurring=(data.get("rrule_option", "Нет") != "Нет"),
        is_nagging=data.get("is_nagging", False),
        rrule_text=data.get("rrule_option", "Нет")
    )
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("del_task_"))
async def callback_delete_task(callback: CallbackQuery, session: AsyncSession):
    task_id = int(callback.data.split("del_task_")[1])
    
    await session.execute(delete(Reminder).where(Reminder.id == task_id))
    await session.commit()
    
    # Используем функцию из scheduler.py
    remove_reminder_job(task_id)
        
    await callback.answer("Задача удалена.")
    await callback.message.edit_text("✅ Задача удалена.", reply_markup=None)


@router.callback_query(F.data == "close_tasks")
async def callback_close_tasks(callback: CallbackQuery):
    await callback.message.delete()

@router.callback_query(F.data == "refresh_tasks")
async def callback_refresh_tasks(callback: CallbackQuery, session: AsyncSession, user: User):
    await btn_my_tasks(callback.message, session, user) # Reuse logic? message edit might differ.
    # Reuse is tricky because one is Message, other Callback keys.
    # Just copy-paste for safety or refactor. Copy-paste logic here:
    
    q = select(Reminder).where(Reminder.user_id == user.id).order_by(Reminder.execution_time)
    result = await session.execute(q)
    tasks = result.scalars().all()
    
    if not tasks:
        await callback.message.edit_text("🎉 У тебя нет активных задач.", reply_markup=None)
        return
    
    text_lines = ["📋 **Твои задачи:**\n"]
    for task in tasks:
        dt_str = task.execution_time.strftime("%d.%m %H:%M")
        recur_icon = "🔁 " if task.is_recurring else ""
        nag_icon = "🔥 " if task.is_nagging else ""
        text_lines.append(f"▫️ `{dt_str}`: {recur_icon}{nag_icon}{task.reminder_text}")
        
    final_text = "\n".join(text_lines)
    await callback.message.edit_text(final_text, reply_markup=get_tasks_list_keyboard(tasks), parse_mode="Markdown")


@router.callback_query(F.data == "settings_change_tz")
async def callback_change_tz(callback: CallbackQuery):
    await callback.message.edit_text("Выбери свой часовой пояс:", reply_markup=get_timezone_keyboard())

@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(callback: CallbackQuery, session: AsyncSession, user: User):
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await callback.message.edit_text("Напиши мне свой город (например 'Europe/London').")
        return

    user.timezone = action
    await session.commit()
    await callback.message.edit_text(f"✅ Часовой пояс: `{action}`", reply_markup=None)
    await callback.answer()

@router.callback_query(F.data.startswith("done_task_"))
async def callback_task_done(callback: CallbackQuery):
    reminder_id = int(callback.data.split("done_task_")[1])
    # Используем функцию из scheduler.py
    remove_nagging_job(reminder_id)
    
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ **Красавчик. Отстал.**", parse_mode="Markdown")
    await callback.answer("Готово!")
