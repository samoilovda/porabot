"""FSM states for the reminder creation wizard."""

from aiogram.fsm.state import State, StatesGroup


class ReminderWizard(StatesGroup):
    entering_text = State()
    choosing_time = State()
    waiting_for_nag_limit = State()
    confirming_parse = State()
    waiting_for_done_note = State()
