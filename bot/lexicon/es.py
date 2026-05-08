"""Spanish dictionary (based on EN fallback with key Spanish overrides)."""

from typing import Any

from bot.lexicon.en import EN

ES: dict[str, Any] = dict(EN)
ES.update(
    {
        # Menu & Commands
        "cmd_start": "👋 Hola, {name}!\nSoy **Porabot**. Te ayudaré a procrastinar menos.\n\nElige una acción del menú 👇",
        "btn_new_task": "➕ Nueva tarea",
        "btn_my_tasks": "📅 Mis tareas",
        "btn_settings": "⚙️ Ajustes",
        "btn_habits": "🫧 Hábitos",

        # Language Selection
        "choose_language": "Por favor, elige un idioma / Please choose a language:",
        "lang_ru": "🇷🇺 Русский",
        "lang_en": "🇬🇧 English",
        "lang_es": "🇪🇸 Español",
        "lang_set": "✅ Idioma cambiado a Español.",

        # Settings
        "settings_text": "⚙️ **Ajustes**\n\n🌍 Tu zona horaria: `{timezone}`\n🗣 Idioma de la interfaz: Español\n😴 Horas de silencio: `{quiet_hours}`\n\nSi la hora de recordatorios cambia, revisa tu zona horaria.",
        "btn_change_tz": "🌍 Cambiar zona horaria",
        "btn_change_lang": "🗣 Cambiar idioma",
        "choose_tz": "Elige tu zona horaria:",
        "tz_manual_prompt": "Envía zona horaria IANA, por ejemplo `Europe/Madrid` o `America/New_York`. También aceptamos `EST`/`ET`.",
        "tz_success": "✅ Zona horaria: `{tz}`",
        "tz_invalid": "❌ Zona horaria desconocida. Usa IANA como `Europe/Madrid` o `America/New_York`, o alias `EST`/`ET`.",
        "btn_toggle_utc_off": "🕒 Mostrar desplazamiento UTC: OFF",
        "btn_toggle_utc_on": "🕒 Mostrar desplazamiento UTC: ON",
        "btn_clear_all": "🗑 Borrar todo",
        "btn_clear_all_confirm": "✅ Sí, borrar todo",
        "btn_clear_all_cancel": "↩ Cancelar",
        "clear_all_confirm_text": "⚠️ Esto eliminará **todas** tus tareas, hábitos y ajustes de forma permanente.\n\n¿Seguro?",
        "clear_all_done": "✅ Todos tus datos se eliminaron. Empezamos desde cero.",

        # Basics
        "what_next": "¿Qué sigue?",
        "cmd_cancel": "❌ Creación de recordatorio cancelada.",
        "btn_cancel": "❌ Cancelar",
        "no_tasks": "🎉 No tienes tareas activas.",
        "tasks_header": "📋 **Tus tareas:**\n",
        "task_done_replies": [
            "✅ **¡Bien ahí!**",
            "✅ **¡Genial!**",
            "✅ **¡Gran avance!**",
            "✅ **¡Muy bien hecho!**",
            "✅ **¡Sigues con ritmo!**",
            "✅ **¡Otro logro!**",
            "✅ **¡Excelente impulso!**",
        ],

        # Habits
        "habits_dashboard": "🫧 **Panel de hábitos**:\nElige una plantilla o crea tu hábito diario.",
        "habit_motivation": "🔥 Impulso: {weekly_done} completadas en 7d · {active_count} hábitos activos · racha {best_current_streak} (máx {best_ever_streak})",
        "habit_preset_water": "💧 Beber agua",
        "habit_preset_workout": "🧘 Entrenar",
        "habit_preset_rest": "🛌 Descansar",
        "habit_btn_custom": "➕ Hábito personalizado",
        "habit_btn_fluid": "🌊 Hábito flexible",
        "habit_btn_list": "📋 Mis hábitos activos",
        "habit_btn_cancel": "❌ Cancelar",
        "habit_cancelled": "Cancelado.",
        "habit_unknown": "❓ Hábito",
        "habit_selected_prompt": "Elegiste: **{habit}**\n\n¿A qué hora debo recordártelo cada día? (por ejemplo `10:00`)",
        "habit_custom_prompt": "¿Qué hábito quieres crear?\n*(por ejemplo: \"Leer 20 páginas\")*",
        "habit_time_prompt": "¡Genial! ¿A qué hora debo recordártelo cada día? (por ejemplo `10:00`)",
        "habit_default_name": "Mi hábito",
        "habit_time_retry": "❌ No pude entender la hora. Inténtalo de nuevo (por ejemplo `10:00`).",
        "habit_created": "✅ **Hábito diario creado**\nTe recordaré **{habit}** cada día a las `{time}`.",
        "habit_fluid_name_prompt": "🌊 Envía el nombre de tu hábito flexible.\n\nEjemplo: `Leer 20 páginas`",
        "habit_fluid_mode_prompt": "Elige el modo del hábito flexible:",
        "habit_fluid_mode_brief_only": "1) Solo resumen de mañana + comprobación de noche",
        "habit_fluid_mode_ask_time": "2) Preguntarme cada mañana la hora de recordatorio",
        "habit_fluid_created": "✅ **Hábito flexible creado**\nHábito: **{habit}**\nModo: {mode}",
        "habit_no_active": "📋 No tienes hábitos diarios activos.",
        "habit_list_header": "📋 **Tus hábitos diarios activos:**\n",
        "habit_list_item": "{index}. **{habit}** ({time}) · 🔥 {streak} (máx {best}) · {mode}",
        "habit_btn_delete_n": "❌ Eliminar {index}",
        "habit_btn_back_dashboard": "🔙 Volver al panel",
        "habit_deleted_alert": "✅ Hábito eliminado.",
        "habit_deleted_followup": "Hábito eliminado. Pulsa 🫧 Hábitos para ver el panel actualizado.",
        "habit_delete_error_alert": "❌ Error al eliminar hábito.",
        "habit_mode_fixed": "hora fija",
        "habit_mode_fluid_ask_time": "flexible: preguntar hora cada mañana",
        "habit_mode_fluid_brief_only": "flexible: solo resúmenes",
        "habit_fluid_time_anytime": "en cualquier momento de hoy",
        "fluid_morning_title": "🌊 **Hábitos flexibles de hoy:**",
        "fluid_pick_time_prompt": "🌊 **Plan del día:**\nElige la hora de recordatorio para: **{habit}**",
        "fluid_evening_check": "🌙 **Antes del resumen nocturno:**\nMarca hábitos flexibles completados:",
        "fluid_done_btn_prefix": "✅ Hecho: ",
        "fluid_done_saved": "✅ Marcado como hecho para hoy.",
        "fluid_time_custom": "⌨️ Hora personalizada",
        "fluid_time_custom_prompt": "Envía la hora para hoy en formato `HH:MM` (por ejemplo `14:30`).",
        "fluid_time_invalid": "❌ Usa formato HH:MM (por ejemplo `14:30`).",
        "fluid_time_past": "❌ Esa hora ya pasó hoy.",
        "fluid_time_saved": "✅ Te recordaré hoy a las `{time}`.",
    }
)
