"""Magic string constants for callback data patterns."""

# =============================================================================
# CALLBACK DATA PATTERN CONSTANTS
# =============================================================================

# Time selection callbacks
TIME_DELTA_15 = "time_delta_15"
TIME_DELTA_30 = "time_delta_30"
TIME_DELTA_60 = "time_delta_60"
TIME_DELTA_120 = "time_delta_120"
TIME_DELTA_180 = "time_delta_180"

# Time selection callbacks (fixed times)
TIME_FIXED_PREFIX = "time_fixed_"
TIME_TOMORROW = "time_tomorrow"
TIME_MANUAL = "time_manual"

# Edit mode callbacks
EDIT_TOGGLE_REPEAT_PREFIX = "edit_toggle_repeat_"
EDIT_TOGGLE_NAGGING_PREFIX = "edit_toggle_nagging_"
EDIT_DELETE_PREFIX = "edit_delete_"

# Task action callbacks
DONE_TASK_PREFIX = "done_task_"
DEL_TASK_PREFIX = "del_task_"
SNOOZE_SHOW_PREFIX = "snooze_show_"
SNOOZE_ACT_PREFIX = "snooze_act_"

# Navigation callbacks
CANCEL_WIZARD = "cancel_wizard"
REFRESH_TASKS = "refresh_tasks"
SHOW_COMPLETED = "show_completed"
CLOSE_TASKS = "close_tasks"

# Settings callbacks
SETTINGS_CHANGE_TZ = "settings_change_tz"
SETTINGS_CHANGE_LANG = "settings_change_lang"
SETTINGS_TOGGLE_UTC = "settings_toggle_utc"

# Language selection callbacks
SET_LANG_RU = "set_lang_ru"
SET_LANG_EN = "set_lang_en"

# Timezone selection callbacks
SET_TZ_PREFIX = "set_tz_"
SET_TZ_MANUAL = "set_tz_manual"


# =============================================================================
# CALLBACK DATA PARSING HELPERS
# =============================================================================

def parse_time_delta_callback(callback_data: str) -> int | None:
    """Parse time delta callback data (e.g., 'time_delta_15' → 15)."""
    if callback_data == TIME_DELTA_15:
        return 15
    elif callback_data == TIME_DELTA_30:
        return 30
    elif callback_data == TIME_DELTA_60:
        return 60
    elif callback_data == TIME_DELTA_120:
        return 120
    elif callback_data == TIME_DELTA_180:
        return 180
    return None


def parse_fixed_time_callback(callback_data: str) -> str | None:
    """Parse fixed time callback data (e.g., 'time_fixed_2024-03-27T18:30:00+03:00' → ISO string)."""
    if not callback_data.startswith(TIME_FIXED_PREFIX):
        return None
    return callback_data[len(TIME_FIXED_PREFIX):]


def parse_edit_toggle_repeat_callback(callback_data: str) -> int | None:
    """Parse edit toggle repeat callback data (e.g., 'edit_toggle_repeat_123' → 123)."""
    if not callback_data.startswith(EDIT_TOGGLE_REPEAT_PREFIX):
        return None
    try:
        return int(callback_data[len(EDIT_TOGGLE_REPEAT_PREFIX):])
    except ValueError:
        return None


def parse_edit_toggle_nagging_callback(callback_data: str) -> int | None:
    """Parse edit toggle nagging callback data (e.g., 'edit_toggle_nagging_123' → 123)."""
    if not callback_data.startswith(EDIT_TOGGLE_NAGGING_PREFIX):
        return None
    try:
        return int(callback_data[len(EDIT_TOGGLE_NAGGING_PREFIX):])
    except ValueError:
        return None


def parse_edit_delete_callback(callback_data: str) -> int | None:
    """Parse edit delete callback data (e.g., 'edit_delete_123' → 123)."""
    if not callback_data.startswith(EDIT_DELETE_PREFIX):
        return None
    try:
        return int(callback_data[len(EDIT_DELETE_PREFIX):])
    except ValueError:
        return None


def parse_done_task_callback(callback_data: str) -> int | None:
    """Parse done task callback data (e.g., 'done_task_123' → 123)."""
    if not callback_data.startswith(DONE_TASK_PREFIX):
        return None
    try:
        return int(callback_data[len(DONE_TASK_PREFIX):])
    except ValueError:
        return None


def parse_del_task_callback(callback_data: str) -> int | None:
    """Parse delete task callback data (e.g., 'del_task_123' → 123)."""
    if not callback_data.startswith(DEL_TASK_PREFIX):
        return None
    try:
        return int(callback_data[len(DEL_TASK_PREFIX):])
    except ValueError:
        return None


def parse_snooze_show_callback(callback_data: str) -> int | None:
    """Parse snooze show callback data (e.g., 'snooze_show_123' → 123)."""
    if not callback_data.startswith(SNOOZE_SHOW_PREFIX):
        return None
    try:
        return int(callback_data[len(SNOOZE_SHOW_PREFIX):])
    except ValueError:
        return None


def parse_snooze_act_callback(callback_data: str) -> tuple[int, str] | None:
    """Parse snooze act callback data (e.g., 'snooze_act_123_15m' → (123, '15m'))."""
    if not callback_data.startswith(SNOOZE_ACT_PREFIX):
        return None
    parts = callback_data[len(SNOOZE_ACT_PREFIX):].split("_")
    if len(parts) >= 2:
        try:
            reminder_id = int(parts[0])
            action = "_".join(parts[1:])  # Rejoin in case action has underscores (e.g., 'custom')
            return (reminder_id, action)
        except ValueError:
            pass
    return None


def parse_timezone_callback(callback_data: str) -> str | None:
    """Parse timezone selection callback data (e.g., 'set_tz_Europe/Moscow' → 'Europe/Moscow')."""
    if not callback_data.startswith(SET_TZ_PREFIX):
        return None
    return callback_data[len(SET_TZ_PREFIX):]


def parse_set_lang_callback(callback_data: str) -> str | None:
    """Parse language selection callback data (e.g., 'set_lang_ru' → 'ru')."""
    if callback_data == SET_LANG_RU:
        return "ru"
    elif callback_data == SET_LANG_EN:
        return "en"
    return None


# =============================================================================
# CALLBACK DATA VALIDATION
# =============================================================================

def is_time_selection_callback(callback_data: str) -> bool:
    """Check if callback data is a time selection option."""
    return (
        callback_data in [TIME_DELTA_15, TIME_DELTA_30, TIME_DELTA_60, 
                          TIME_DELTA_120, TIME_DELTA_180] or
        callback_data.startswith(TIME_FIXED_PREFIX) or
        callback_data == TIME_TOMORROW or
        callback_data == TIME_MANUAL
    )


def is_edit_callback(callback_data: str) -> bool:
    """Check if callback data is an edit mode option."""
    return (
        callback_data.startswith(EDIT_TOGGLE_REPEAT_PREFIX) or
        callback_data.startswith(EDIT_TOGGLE_NAGGING_PREFIX) or
        callback_data.startswith(EDIT_DELETE_PREFIX)
    )


def is_task_action_callback(callback_data: str) -> bool:
    """Check if callback data is a task action (done/delete)."""
    return (
        callback_data.startswith(DONE_TASK_PREFIX) or
        callback_data.startswith(DEL_TASK_PREFIX)
    )


def is_navigation_callback(callback_data: str) -> bool:
    """Check if callback data is a navigation option."""
    return callback_data in [CANCEL_WIZARD, REFRESH_TASKS, SHOW_COMPLETED, CLOSE_TASKS]


def is_settings_callback(callback_data: str) -> bool:
    """Check if callback data is a settings option."""
    return (
        callback_data == SETTINGS_CHANGE_TZ or
        callback_data == SETTINGS_CHANGE_LANG or
        callback_data == SETTINGS_TOGGLE_UTC
    )


def is_language_selection_callback(callback_data: str) -> bool:
    """Check if callback data is a language selection option."""
    return callback_data in [SET_LANG_RU, SET_LANG_EN]


def is_timezone_selection_callback(callback_data: str) -> bool:
    """Check if callback data is a timezone selection option."""
    return (
        callback_data.startswith(SET_TZ_PREFIX) or
        callback_data == SET_TZ_MANUAL
    )