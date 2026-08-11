"""FSM states for the reminder creation wizard."""

from aiogram.fsm.state import State, StatesGroup


class ReminderWizard(StatesGroup):
    entering_text = State()
    choosing_time = State()
    waiting_for_nag_limit = State()
    confirming_parse = State()
    waiting_for_done_note = State()
    # 3.1: RRULE builder — free-text steps of the repeat-configuration flow.
    waiting_for_repeat_interval = State()
    waiting_for_repeat_monthday = State()
    waiting_for_repeat_end_count = State()
    waiting_for_repeat_end_until = State()
