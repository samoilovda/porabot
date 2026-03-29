"""Russian dictionary (default)."""

from typing import Any

RU: dict[str, Any] = {
    # Menu & Commands
    "cmd_start": "👋 Привет, {name}!\nЯ **Porabot**. Я помогу тебе не прокрастинировать.\n\nВыбери действие в меню 👇",
    "btn_new_task": "➕ Новая задача",
    "btn_my_tasks": "📅 Мои задачи",
    "btn_settings": "⚙️ Настройки",
    "btn_habits": "🫧 Привычки",
    
    # Language Selection
    "choose_language": "Пожалуйста, выберите язык / Please choose a language:",
    "lang_ru": "🇷🇺 Русский",
    "lang_en": "🇬🇧 English",
    "lang_set": "✅ Язык установлен на Русский.",

    # Settings
    "settings_text": "⚙️ **Настройки**\n\n🌍 Твой часовой пояс: `{timezone}`\n🗣 Язык интерфейса: Русский\n\nЕсли время напоминаний скачет, проверь пояс.",
    "btn_change_tz": "🌍 Сменить часовой пояс",
    "btn_change_lang": "🗣 Сменить язык",
    "choose_tz": "Выбери свой часовой пояс:",
    "tz_manual_prompt": "Напиши мне свой город (например 'Europe/London').",
    "tz_success": "✅ Часовой пояс: `{tz}`",
    "btn_toggle_utc_off": "🕒 Показывать смещение UTC: ВЫКЛ",
    "btn_toggle_utc_on": "🕒 Показывать смещение UTC: ВКЛ",

    # Tasks List
    "no_tasks": "🎉 У тебя нет активных задач. Отдыхай!",
    "tasks_header": "📋 **Твои задачи:**\n",
    "btn_delete_prefix": "🗑 Удалить:",
    "btn_refresh": "🔄 Обновить",
    "btn_close": "❌ Закрыть",

    # Wizard
    "enter_task": "Напиши, о чем напомнить.\nМожно сразу с временем: *\"Позвонить маме завтра в 18:00\"*.",
    "parse_error": "Ошибка обработки текста. Проверь формат.",
    "ask_time": "Ок, задача: \"{text}\".\nКогда напомнить?",
    "try_again_manual": "Попробуй снова написать задачу ЦЕЛИКОМ, включая дату и время.",
    
    # Confirmation logic -> Edit Logic
    "preview": "✅ **Задача сохранена!**\n📌 {text}\n⏰ {time}",
    "btn_repeat_prefix": "🔁 Повтор:",
    "btn_nagging_prefix": "{icon} Зуд (Nagging):",
    "btn_delete": "🗑 Удалить",
    "status_on": "ВКЛ",
    "status_off": "ВЫКЛ",
    "repeat_none": "Нет",
    "repeat_day": "День",
    "repeat_weekdays": "Будни",
    "repeat_week": "Неделя",
    
    "main_menu_fallback": "Главное меню",
    "task_deleted": "❌ Задача удалена.",  # single definition — canonical value
    "what_next": "Что дальше?",
    "cmd_cancel": "❌ Создание напоминания отменено.",
    "btn_cancel": "❌ Отмена",

    # Time Selection Keyboard
    "time_morning": "🌅 Утро",
    "time_day": "☀️ День",
    "time_evening": "🌙 Вечер",
    "time_night": "🌌 Ночь",
    "time_tomorrow": "🗓 Завтра (09:00)",
    "time_manual": "⌨️ Ввести вручную",

    # Reminders / Scheduler
    "reminder_prefix": "🔔 **ПОРА!**\n",
    "btn_done": "✅ Сделано",
    "btn_snooze": "⏰ Отложить",
    "task_done_reply": "✅ **Красавчик. Отстал.**",
    "already_done": "Уже выполнено ✅",
    "snoozed_until": "⏰ Отложено до {time}",

    # Snooze options
    "snooze_15m": "+15м",
    "snooze_30m": "+30м",
    "snooze_1h": "+1ч",
    "snooze_2h": "+2ч",
    "snooze_1d": "+1 день",
    "snooze_morning": "🌅 Утро",
    "snooze_day": "☀️ День",
    "snooze_evening": "🌙 Вечер",
    "snooze_night": "🌌 Ночь",
    "snooze_custom": "⌨️ Свой вариант",

    # Completed tasks section
    "btn_completed_tasks": "📜 Выполненные",
    "no_completed_tasks": "🧨 Выполненных задач еще нет. Давай исправлять это!",
    "completed_header": "🏆 **Выполненные задачи:**\n",
    "btn_done_task_prefix": "✅ Готово:",
    
    # Error messages (SECURITY FIX: Added missing keys)
    "text_too_long": "❌ Текст слишком длинный ({length} символов). Максимум: {max_length} символов.",
    "schedule_error": "❌ Не удалось запланировать напоминание. Попробуйте снова.",
}
