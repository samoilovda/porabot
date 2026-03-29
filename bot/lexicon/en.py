"""English dictionary."""

from typing import Any

EN: dict[str, Any] = {
    # Menu & Commands
    "cmd_start": "👋 Hi, {name}!\nI'm **Porabot**. I'll help you stop procrastinating.\n\nChoose an action from the menu 👇",
    "btn_new_task": "➕ New Task",
    "btn_my_tasks": "📅 My Tasks",
    "btn_settings": "⚙️ Settings",
    "btn_habits": "🫧 Habits",

    # Language Selection
    "choose_language": "Please choose a language / Пожалуйста, выберите язык:",
    "lang_ru": "🇷🇺 Русский",
    "lang_en": "🇬🇧 English",
    "lang_set": "✅ Language set to English.",

    # Settings
    "settings_text": "⚙️ **Settings**\n\n🌍 Your timezone: `{timezone}`\n🗣 Interface language: English\n\nIf reminder times jump around, check your timezone.",
    "btn_change_tz": "🌍 Change Timezone",
    "btn_change_lang": "🗣 Change Language",
    "choose_tz": "Choose your timezone:",
    "tz_manual_prompt": "Send me your city (e.g., 'Europe/London').",
    "tz_success": "✅ Timezone: `{tz}`",
    "btn_toggle_utc_off": "🕒 Show UTC offset: OFF",
    "btn_toggle_utc_on": "🕒 Show UTC offset: ON",

    # Tasks List
    "no_tasks": "🎉 You have no active tasks. Relax!",
    "tasks_header": "📋 **Your tasks:**\n",
    "btn_delete_prefix": "🗑 Delete:",
    "btn_refresh": "🔄 Refresh",
    "btn_close": "❌ Close",

    # Wizard
    "enter_task": "Write what to remind you about.\nYou can include time: *\"Call mom tomorrow at 18:00\"*.",
    "parse_error": "Error parsing text. Check the format.",
    "ask_time": "Ok, task: \"{text}\".\nWhen to remind?",
    "try_again_manual": "Try again to write the FULL task, including date and time.",

    # Confirmation logic -> Edit logic
    "preview": "✅ **Task saved!**\n📌 {text}\n⏰ {time}",
    "btn_repeat_prefix": "🔁 Repeat:",
    "btn_nagging_prefix": "{icon} Nagging:",
    "btn_delete": "🗑 Delete",
    "status_on": "ON",
    "status_off": "OFF",
    "repeat_none": "None",
    "repeat_day": "Daily",
    "repeat_weekdays": "Weekdays",
    "repeat_week": "Weekly",

    "main_menu_fallback": "Main menu",
    "task_deleted": "❌ Task deleted.",  # single definition — canonical value
    "what_next": "What next?",
    "cmd_cancel": "❌ Reminder creation cancelled.",
    "btn_cancel": "❌ Cancel",

    # Time Selection Keyboard
    "time_morning": "🌅 Morning",
    "time_day": "☀️ Day",
    "time_evening": "🌙 Evening",
    "time_night": "🌌 Night",
    "time_tomorrow": "🗓 Tomorrow (09:00)",
    "time_manual": "⌨️ Enter manually",

    # Reminders / Scheduler
    "reminder_prefix": "🔔 **IT'S TIME!**\n",
    "btn_done": "✅ Done",
    "btn_snooze": "⏰ Snooze",
    "task_done_reply": "✅ **Good job. I'll leave you alone.**",
    "already_done": "Already done ✅",
    "snoozed_until": "⏰ Snoozed until {time}",

    # Snooze options
    "snooze_15m": "+15m",
    "snooze_30m": "+30m",
    "snooze_1h": "+1h",
    "snooze_2h": "+2h",
    "snooze_1d": "+1 day",
    "snooze_morning": "🌅 Morning",
    "snooze_day": "☀️ Day",
    "snooze_evening": "🌙 Evening",
    "snooze_night": "🌌 Night",
    "snooze_custom": "⌨️ Custom",

    # Completed tasks section
    "btn_completed_tasks": "📜 Completed",
    "no_completed_tasks": "🧨 No completed tasks yet. Let's change that!",
    "completed_header": "🏆 **Completed tasks:**\n",
    "btn_done_task_prefix": "✅ Done:",
    
    # Error messages (SECURITY FIX: Added missing keys)
    "text_too_long": "❌ Text too long ({length} chars). Maximum: {max_length} chars.",
    "schedule_error": "❌ Failed to schedule reminder. Please try again.",
}
