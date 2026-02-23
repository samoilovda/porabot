import asyncio
import logging
from datetime import datetime, timedelta
import pytz

# Импорты из loader.py для предотвращения циклических зависимостей
from loader import scheduler, async_session_maker, bot, logger
from models import Reminder
from keyboards import get_task_done_keyboard

from sqlalchemy import select, delete
from dateutil.rrule import rrulestr
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# --- Helper Functions ---

async def send_telegram_message(user_id: int, text: str, reply_markup=None):
    """
    Отправляет сообщение пользователю через Telegram API.
    Обрабатывает блокировку бота пользователем.
    """
    if not bot:
        logger.warning("Bot instance is not initialized (no token). Skipping message sending.")
        return

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"🔔 **ПОРА!**\n{text}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except TelegramForbiddenError:
        logger.warning(f"User {user_id} has blocked the bot. Message not sent.")
        # Тут можно добавить логику удаления пользователя или пометки "inactive"
        # Для MVP просто логируем.
    except TelegramBadRequest as e:
        logger.error(f"Bad request when sending to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Failed to send message to {user_id}: {e}", exc_info=True)


async def send_reminder_wrapper(reminder_id: int):
    """
    Обертка задачи. Вызывается APScheduler'ом.
    Работает ВНЕ контекста Telegram-сообщения, поэтому создает свою сессию.
    """
    logger.info(f"Executing reminder job for ID: {reminder_id}")
    
    async with async_session_maker() as session:
        try:
            # Получаем напоминание из БД
            result = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
            reminder = result.scalar_one_or_none()

            if not reminder:
                logger.warning(f"Reminder {reminder_id} not found in DB. Skipping execution.")
                # Если задачи нет в БД, нужно удалить её и из планировщика?
                # APScheduler обычно сам удаляет job, если он 'date' и выполнился.
                # Если это recurring job в APScheduler, то он останется.
                # Но мы используем 'date' триггеры и перепланируем вручную.
                return

            # 1. Отправляем сообщение
            keyboard = None
            if reminder.is_nagging:
                keyboard = get_task_done_keyboard(reminder.id)
            
            await send_telegram_message(reminder.user_id, reminder.reminder_text, reply_markup=keyboard)

            # 2. Обработка RECURRING (повторяющихся задач)
            if reminder.is_recurring and reminder.rrule_string:
                try:
                    # dateutil.rrule работает с naive datetime по умолчанию, если dtstart naive.
                    # Но у нас в БД aware datetime (или naive, считающийся UTC?).
                    # SQLAlchemy + aiosqlite: обычно сохраняет как строку без зоны или UTC.
                    # В models.py мы не форсировали timezone=True для DateTime.
                    # Лучше считать, что в БД хранится Native (Naive) время, 
                    # но мы знаем часовой пояс юзера (в User модели).
                    # Однако Reminder не хранит таймзону.
                    # Поэтому execution_time лучше хранить в UTC.
                    
                    # Получим execution_time. Если оно naive, считаем его UTC (или Local?).
                    # В коде handlers.py мы делаем .fromisoformat(), который сохраняет offset.
                    start_dt = reminder.execution_time
                    
                    # rrule требует, чтобы dtstart и now были одной "зоны" (aware/naive).
                    if start_dt.tzinfo is None:
                         # Если в БД naive, считаем что это UTC (Best Practice)
                         # или Local? Для упрощения считаем Naive = Local для rrule (но это опасно).
                         # Лучше приведем к UTC.
                         start_dt = start_dt.replace(tzinfo=pytz.UTC)

                    rule = rrulestr(reminder.rrule_string, dtstart=start_dt)
                    
                    # Текущее время тоже должно быть aware
                    now = datetime.now(start_dt.tzinfo)
                    
                    next_run = rule.after(now)

                    if next_run:
                        logger.info(f"Rescheduling RECURRING reminder {reminder_id} to {next_run}")
                        
                        # Обновляем время в БД
                        # Важно: next_run должен быть совместим с типом поля в БД.
                        # Если поле Naive, убираем зону перед сохранением?
                        # SQLAlchemy DateTime(timezone=False) нормально съедает Aware и режет зону (обычно warning)
                        # или сохраняет. Лучше привести к UTC naive если движок sqlite.
                        # Но пока оставим как есть, проверив совместимость.
                        
                        reminder.execution_time = next_run
                        await session.commit()

                        # Планируем основную задачу
                        schedule_reminder(reminder_id, next_run, is_nagging=reminder.is_nagging)
                    else:
                        logger.info(f"No next occurrence for reminder {reminder_id}.")
                        
                except Exception as e:
                    logger.error(f"Error calculating next run time for reminder {reminder_id}: {e}", exc_info=True)

            # 3. Обработка NAGGING (зуд)
            # Если включен зуд, планируем "пинок" через 5 минут.
            if reminder.is_nagging:
                # Используем таймзону execution_time или UTC
                tz = reminder.execution_time.tzinfo or pytz.UTC
                # next_nag через 5 минут от ТЕКУЩЕГО момента
                next_nag = datetime.now(tz) + timedelta(minutes=5)
                
                logger.info(f"Scheduling NAGGING for reminder {reminder_id} at {next_nag}")
                
                # Используем add_job напрямую
                scheduler.add_job(
                    send_reminder_wrapper,
                    'date',
                    run_date=next_nag,
                    args=[reminder_id],
                    id=f"nag_{reminder_id}", 
                    replace_existing=True
                )
                
        except Exception as e:
            logger.error(f"Generic error in reminder wrapper for {reminder_id}: {e}", exc_info=True)


def schedule_reminder(reminder_id: int, run_date: datetime, is_nagging: bool = False):
    """
    Планирует напоминание.
    Инкапсулирует логику добавления задачи в планировщик.
    """
    # SQLite не любит DateTime с таймзоной, если адаптеры не настроены.
    # Но APScheduler нормально сериализует.
    # Главное, чтобы run_date был либо aware, либо naive (scheduler conf).
    
    try:
        scheduler.add_job(
            send_reminder_wrapper,
            'date',
            run_date=run_date,
            args=[reminder_id],
            id=str(reminder_id),
            replace_existing=True
        )
        logger.info(f"Scheduled reminder {reminder_id} for {run_date}")
    except Exception as e:
         logger.error(f"Failed to schedule reminder {reminder_id}: {e}", exc_info=True)

def remove_nagging_job(reminder_id: int):
    try:
        scheduler.remove_job(f"nag_{reminder_id}")
        logger.info(f"Removed nagging job for reminder {reminder_id}")
    except Exception:
        pass 

def remove_reminder_job(reminder_id: int):
    try:
        scheduler.remove_job(str(reminder_id))
        logger.info(f"Removed job for reminder {reminder_id}")
    except Exception:
        pass
    
    # Также пытаемся удалить nagging, если он есть
    remove_nagging_job(reminder_id)
