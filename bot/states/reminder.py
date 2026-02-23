"""FSM states for the reminder creation wizard."""

from aiogram.fsm.state import State, StatesGroup


class ReminderWizard(StatesGroup):
    entering_text = State()
    choosing_time = State()
    confirming = State()
