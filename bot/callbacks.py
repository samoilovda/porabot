"""Central registry of all typed CallbackData factories for Porabot.

Using aiogram's CallbackData instead of raw f-strings eliminates a whole class of
bugs: IndexError from split(), silent format mismatches, and potential 64-byte
overflows (ISO datetime strings were close to the limit).

Packed format: ``prefix:field1:field2:...``  (aiogram default separator is ``:``)
All packed strings are well under the 64-byte Telegram limit.
"""

from typing import Optional
from aiogram.filters.callback_data import CallbackData


# ---------------------------------------------------------------------------
# Time wizard  (FSM: ReminderWizard.choosing_time)
# ---------------------------------------------------------------------------

class TimeDeltaCallback(CallbackData, prefix="td"):
    """Add *minutes* to the current time."""
    minutes: int


class TimeFixedCallback(CallbackData, prefix="tf"):
    """Fire at a fixed moment; *ts* is a Unix UTC timestamp (int seconds)."""
    ts: int


# ---------------------------------------------------------------------------
# Timezone / language onboarding
# ---------------------------------------------------------------------------

class SetTimezoneCallback(CallbackData, prefix="set_tz"):
    tz: str   # IANA tz string (e.g. "Europe/Moscow") or "manual"


class SetLangCallback(CallbackData, prefix="set_lang"):
    lang: str   # ISO 639-1 code: "ru", "en", "es"


# ---------------------------------------------------------------------------
# Task edit keyboard
# ---------------------------------------------------------------------------

class EditReminderCallback(CallbackData, prefix="edit"):
    action: str       # toggle_repeat | toggle_nagging | set_nag_limit | delete
    reminder_id: int


class TaskSettingsCallback(CallbackData, prefix="task_settings"):
    reminder_id: int


class DeleteTaskCallback(CallbackData, prefix="del_task"):
    reminder_id: int


# ---------------------------------------------------------------------------
# Mark-done and follow-up actions
# ---------------------------------------------------------------------------

class DoneTaskCallback(CallbackData, prefix="done_task"):
    reminder_id: int
    cycle_due_ts: Optional[int] = None   # None for non-habit reminders


class DoneNoteCallback(CallbackData, prefix="done_note"):
    reminder_id: int


class DoneSkipNextCallback(CallbackData, prefix="done_skip_next"):
    reminder_id: int


class DoneUndoCallback(CallbackData, prefix="done_undo"):
    reminder_id: int


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

class SnoozeShowCallback(CallbackData, prefix="snooze_show"):
    reminder_id: int


class SnoozeActCallback(CallbackData, prefix="snooze_act"):
    reminder_id: int
    action: str   # 15m | 30m | 1h | 2h | morning | day | evening | night | 1d | custom


# ---------------------------------------------------------------------------
# Fluid habits
# ---------------------------------------------------------------------------

class FluidPickTimeCallback(CallbackData, prefix="fluid_pick_t"):
    reminder_id: int
    hhmm: str   # zero-padded 4-char string, e.g. "0900"


class FluidPickCustomCallback(CallbackData, prefix="fluid_pick_c"):
    reminder_id: int


class FluidDoneCallback(CallbackData, prefix="fluid_done"):
    reminder_id: int


# ---------------------------------------------------------------------------
# Habit management
# ---------------------------------------------------------------------------

class HabitPresetCallback(CallbackData, prefix="habit_preset"):
    key: str   # workout | water | rest


class HabitFluidModeCallback(CallbackData, prefix="habit_fluid_mode"):
    mode: str   # brief_only | ask_time


class DelHabitCallback(CallbackData, prefix="del_habit"):
    reminder_id: int
