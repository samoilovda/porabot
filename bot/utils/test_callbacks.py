"""Round-trip pack/unpack tests for all CallbackData factories.

Each test verifies that pack() → unpack() returns the original field values
and that the packed string stays within Telegram's 64-byte limit.
"""

import pytest
from bot.callbacks import (
    TimeDeltaCallback,
    TimeFixedCallback,
    SetTimezoneCallback,
    SetLangCallback,
    EditReminderCallback,
    TaskSettingsCallback,
    DeleteTaskCallback,
    DoneTaskCallback,
    DoneNoteCallback,
    DoneSkipNextCallback,
    DoneUndoCallback,
    SnoozeShowCallback,
    SnoozeActCallback,
    FluidPickTimeCallback,
    FluidPickCustomCallback,
    FluidDoneCallback,
    HabitPresetCallback,
    HabitFluidModeCallback,
    DelHabitCallback,
)

MAX_BYTES = 64


def _check_length(packed: str) -> None:
    assert len(packed.encode()) <= MAX_BYTES, (
        f"Packed callback data exceeds 64 bytes: {packed!r} ({len(packed.encode())} bytes)"
    )


# ---------------------------------------------------------------------------
# Time wizard
# ---------------------------------------------------------------------------

def test_time_delta_callback_roundtrip():
    cb = TimeDeltaCallback(minutes=30)
    packed = cb.pack()
    _check_length(packed)
    restored = TimeDeltaCallback.unpack(packed)
    assert restored.minutes == 30


def test_time_delta_callback_negative():
    cb = TimeDeltaCallback(minutes=-15)
    packed = cb.pack()
    _check_length(packed)
    restored = TimeDeltaCallback.unpack(packed)
    assert restored.minutes == -15


def test_time_fixed_callback_roundtrip():
    ts = 1700000000
    cb = TimeFixedCallback(ts=ts)
    packed = cb.pack()
    _check_length(packed)
    restored = TimeFixedCallback.unpack(packed)
    assert restored.ts == ts


# ---------------------------------------------------------------------------
# Timezone / language
# ---------------------------------------------------------------------------

def test_set_timezone_callback_roundtrip():
    cb = SetTimezoneCallback(tz="Europe/Moscow")
    packed = cb.pack()
    _check_length(packed)
    restored = SetTimezoneCallback.unpack(packed)
    assert restored.tz == "Europe/Moscow"


def test_set_timezone_callback_manual():
    cb = SetTimezoneCallback(tz="manual")
    packed = cb.pack()
    _check_length(packed)
    assert SetTimezoneCallback.unpack(packed).tz == "manual"


def test_set_lang_callback_roundtrip():
    for lang in ("ru", "en", "es"):
        cb = SetLangCallback(lang=lang)
        packed = cb.pack()
        _check_length(packed)
        assert SetLangCallback.unpack(packed).lang == lang


# ---------------------------------------------------------------------------
# Task edit
# ---------------------------------------------------------------------------

def test_edit_reminder_callback_roundtrip():
    cb = EditReminderCallback(action="toggle_repeat", reminder_id=42)
    packed = cb.pack()
    _check_length(packed)
    restored = EditReminderCallback.unpack(packed)
    assert restored.action == "toggle_repeat"
    assert restored.reminder_id == 42


def test_task_settings_callback_roundtrip():
    cb = TaskSettingsCallback(reminder_id=999)
    packed = cb.pack()
    _check_length(packed)
    assert TaskSettingsCallback.unpack(packed).reminder_id == 999


def test_delete_task_callback_roundtrip():
    cb = DeleteTaskCallback(reminder_id=1)
    packed = cb.pack()
    _check_length(packed)
    assert DeleteTaskCallback.unpack(packed).reminder_id == 1


# ---------------------------------------------------------------------------
# Done actions
# ---------------------------------------------------------------------------

def test_done_task_callback_without_cycle_ts():
    cb = DoneTaskCallback(reminder_id=7)
    packed = cb.pack()
    _check_length(packed)
    restored = DoneTaskCallback.unpack(packed)
    assert restored.reminder_id == 7
    assert restored.cycle_due_ts is None


def test_done_task_callback_with_cycle_ts():
    cb = DoneTaskCallback(reminder_id=7, cycle_due_ts=1700000000)
    packed = cb.pack()
    _check_length(packed)
    restored = DoneTaskCallback.unpack(packed)
    assert restored.reminder_id == 7
    assert restored.cycle_due_ts == 1700000000


def test_done_note_callback_roundtrip():
    cb = DoneNoteCallback(reminder_id=5)
    packed = cb.pack()
    _check_length(packed)
    assert DoneNoteCallback.unpack(packed).reminder_id == 5


def test_done_skip_next_callback_roundtrip():
    cb = DoneSkipNextCallback(reminder_id=11)
    packed = cb.pack()
    _check_length(packed)
    assert DoneSkipNextCallback.unpack(packed).reminder_id == 11


def test_done_undo_callback_roundtrip():
    cb = DoneUndoCallback(reminder_id=22)
    packed = cb.pack()
    _check_length(packed)
    assert DoneUndoCallback.unpack(packed).reminder_id == 22


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

def test_snooze_show_callback_roundtrip():
    cb = SnoozeShowCallback(reminder_id=3)
    packed = cb.pack()
    _check_length(packed)
    assert SnoozeShowCallback.unpack(packed).reminder_id == 3


def test_snooze_act_callback_roundtrip():
    for action in ("15m", "30m", "1h", "2h", "morning", "day", "evening", "night", "1d", "custom"):
        cb = SnoozeActCallback(reminder_id=10, action=action)
        packed = cb.pack()
        _check_length(packed)
        restored = SnoozeActCallback.unpack(packed)
        assert restored.action == action
        assert restored.reminder_id == 10


# ---------------------------------------------------------------------------
# Fluid habits
# ---------------------------------------------------------------------------

def test_fluid_pick_time_callback_roundtrip():
    cb = FluidPickTimeCallback(reminder_id=4, hhmm="0930")
    packed = cb.pack()
    _check_length(packed)
    restored = FluidPickTimeCallback.unpack(packed)
    assert restored.reminder_id == 4
    assert restored.hhmm == "0930"


def test_fluid_pick_custom_callback_roundtrip():
    cb = FluidPickCustomCallback(reminder_id=4)
    packed = cb.pack()
    _check_length(packed)
    assert FluidPickCustomCallback.unpack(packed).reminder_id == 4


def test_fluid_done_callback_roundtrip():
    cb = FluidDoneCallback(reminder_id=4)
    packed = cb.pack()
    _check_length(packed)
    assert FluidDoneCallback.unpack(packed).reminder_id == 4


# ---------------------------------------------------------------------------
# Habit management
# ---------------------------------------------------------------------------

def test_habit_preset_callback_roundtrip():
    for key in ("workout", "water", "rest"):
        cb = HabitPresetCallback(key=key)
        packed = cb.pack()
        _check_length(packed)
        assert HabitPresetCallback.unpack(packed).key == key


def test_habit_fluid_mode_callback_roundtrip():
    for mode in ("brief_only", "ask_time"):
        cb = HabitFluidModeCallback(mode=mode)
        packed = cb.pack()
        _check_length(packed)
        assert HabitFluidModeCallback.unpack(packed).mode == mode


def test_del_habit_callback_roundtrip():
    cb = DelHabitCallback(reminder_id=99)
    packed = cb.pack()
    _check_length(packed)
    assert DelHabitCallback.unpack(packed).reminder_id == 99


# ---------------------------------------------------------------------------
# Large reminder_id edge case (10-digit IDs are possible)
# ---------------------------------------------------------------------------

def test_large_reminder_id_stays_within_64_bytes():
    large_id = 9_999_999_999
    for cls in (TaskSettingsCallback, DeleteTaskCallback, SnoozeShowCallback, DoneNoteCallback):
        packed = cls(reminder_id=large_id).pack()
        _check_length(packed)
