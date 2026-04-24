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
    "settings_text": "⚙️ **Настройки**\n\n🌍 Твой часовой пояс: `{timezone}`\n🗣 Язык интерфейса: Русский\n😴 Тихие часы: `{quiet_hours}`\n\nЕсли время напоминаний скачет, проверь пояс.",
    "btn_change_tz": "🌍 Сменить часовой пояс",
    "btn_change_lang": "🗣 Сменить язык",
    "btn_quiet_hours_setup": "😴 Тихие часы",
    "btn_quiet_on": "😴 Тихие часы: ВКЛ",
    "btn_quiet_off": "😴 Тихие часы: ВЫКЛ",
    "btn_quiet_start": "🌙 Начало: {time}",
    "btn_quiet_end": "🌅 Конец: {time}",
    "quiet_hours_summary": "{status} ({start}–{end})",
    "quiet_time_prompt": "Напиши время для тихих часов в формате HH:MM (например, `23:00`).",
    "quiet_time_invalid": "❌ Введи время в формате HH:MM (например, `23:00`).",
    "quiet_time_saved": "✅ Время тихих часов обновлено: `{time}`",
    "choose_tz": "Выбери свой часовой пояс:",
    "tz_manual_prompt": "Напиши мне свой город (например 'Europe/London').",
    "tz_success": "✅ Часовой пояс: `{tz}`",
    "btn_toggle_utc_off": "🕒 Показывать смещение UTC: ВЫКЛ",
    "btn_toggle_utc_on": "🕒 Показывать смещение UTC: ВКЛ",
    
    # Custom Daily Briefs Settings
    "btn_briefs_setup": "📋 Настройка сводок",
    "btn_briefs_on": "🔔 Ежедневные сводки: ВКЛ",
    "btn_briefs_off": "🔕 Ежедневные сводки: ВЫКЛ",
    "btn_morning_brief": "🌅 Утренняя: {time}",
    "btn_evening_brief": "🌙 Вечерняя: {time}",
    "choose_hour": "Напиши точное время для сводки (Например '09:30' или '23:45'):",
    "btn_back_settings": "🔙 Назад",

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
    "btn_nagging_repeats_prefix": "🔁 Повторы зуда: {count}",
    "btn_delete": "🗑 Удалить",
    "btn_task_settings": "⚙️ Настройки",
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
    "reminder_prefix": "🔔 ",
    "brief_morning": "🌅 **Доброе утро! План на сегодня:**\n",
    "brief_evening_title": "🌙 **Итоги дня:**",
    "brief_evening_done": "✅ Выполнено: {count}",
    "brief_evening_pending": "⏳ Осталось/Пропущено: {count}\n",
    "btn_done": "✅ Сделано",
    "btn_done_add_note": "📝 Добавить заметку",
    "btn_done_skip_next": "⏭ Пропустить следующий",
    "btn_done_undo": "↩ Отменить",
    "btn_snooze": "⏰ Отложить",
    "task_done_reply": "✅ **Красавчик. Отстал.**",
    "already_done": "Уже выполнено ✅",
    "snoozed_until": "⏰ Отложено до {time}",
    "done_note_prompt": "Отправь короткую заметку о выполнении.",
    "done_note_saved": "✅ Заметка сохранена.",
    "done_skip_next_done": "⏭ Следующее срабатывание пропущено. Новое время: {time}",
    "done_skip_next_failed": "❌ Не удалось пропустить следующее срабатывание.",
    "done_undo_done": "↩ Отметка выполнения отменена. Задача снова активна.",
    "task_settings_title": "⚙️ Настройки задачи: {text}",
    "nagging_limit_prompt": "Сколько повторных сообщений присылать для этой задачи? Отправь число от {min} до {max}. Сейчас: {count}.",
    "nagging_limit_invalid": "❌ Отправь число от {min} до {max}.",
    "nagging_limit_updated": "✅ Лимит повторов зуда установлен: {count}",

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
    "parse_confirmation_prompt": "Я мог распознать время неоднозначно.\n\n📌 Задача: {text}\n⏰ Время: {time}\n🎯 Уверенность: {confidence}%\n\nСохраняем так?",
    "btn_parse_confirm_yes": "✅ Да",
    "btn_parse_confirm_time": "🕒 Выбрать время",
    "btn_parse_confirm_cancel": "❌ Отмена",

    # Missed-task recovery
    "missed_recovery_title": "📎 **Восстановление пропущенных задач**\nУ тебя осталось {count} просроченных задач:",
    "btn_recovery_done_all": "✅ Сделать всё",
    "btn_recovery_snooze_all": "⏰ +1ч всем",
    "recovery_done_all_done": "✅ Отмечено выполненными: {count} просроченных задач.",
    "recovery_snooze_all_done": "⏰ Отложено на 1 час: {count} просроченных задач.",

    # Completed tasks section
    "btn_completed_tasks": "📜 Выполненные",
    "no_completed_tasks": "🧨 Выполненных задач еще нет. Давай исправлять это!",
    "completed_header": "🏆 **Выполненные задачи (за 7 дней):**\n",
    "btn_done_task_prefix": "✅ Готово:",

    # Habits
    "habits_dashboard": "🫧 **Панель привычек**:\nВыбери шаблон или создай свою ежедневную привычку.",
    "habit_motivation": "🔥 Импульс: {weekly_done} выполнений за 7 дней · {active_count} активных привычек",
    "habit_preset_water": "💧 Пить воду",
    "habit_preset_workout": "🧘 Тренировка",
    "habit_preset_rest": "🛌 Отдых",
    "habit_btn_custom": "➕ Своя привычка",
    "habit_btn_list": "📋 Мои активные привычки",
    "habit_btn_cancel": "❌ Отмена",
    "habit_cancelled": "Отменено.",
    "habit_unknown": "❓ Привычка",
    "habit_selected_prompt": "Выбрано: **{habit}**\n\nВо сколько напоминать каждый день? (например, `10:00`)",
    "habit_custom_prompt": "Какую привычку хочешь добавить?\n*(например: \"Читать 20 страниц\" или \"Учить Python\")*",
    "habit_time_prompt": "Отлично! Во сколько напоминать каждый день? (например, `10:00`)",
    "habit_default_name": "Моя привычка",
    "habit_time_retry": "❌ Не удалось распознать время. Попробуй снова (например, `10:00`).",
    "habit_created": "✅ **Ежедневная привычка создана!**\nБуду напоминать: **{habit}** каждый день в `{time}`.",
    "habit_create_failed_long": "❌ Не удалось создать привычку (слишком длинный текст).",
    "habit_create_failed_internal": "❌ Внутренняя ошибка при создании привычки.",
    "habit_no_active": "📋 Активных ежедневных привычек пока нет. Нажми «➕ Своя привычка» или выбери шаблон.",
    "habit_list_header": "📋 **Твои активные ежедневные привычки:**\n",
    "habit_list_item": "{index}. **{habit}** (Каждый день в {time})",
    "habit_btn_delete_n": "❌ Удалить {index}",
    "habit_btn_back_dashboard": "🔙 Назад к панели",
    "habit_deleted_alert": "✅ Привычка удалена!",
    "habit_deleted_followup": "Привычка удалена. Нажми 🫧 Привычки, чтобы открыть обновлённую панель.",
    "habit_delete_error_alert": "❌ Ошибка при удалении привычки.",
    
    # Error messages (SECURITY FIX: Added missing keys)
    "text_too_long": "❌ Текст слишком длинный ({length} символов). Максимум: {max_length} символов.",
    "schedule_error": "❌ Не удалось запланировать напоминание. Попробуйте снова.",
}
