"""FSM states for the reminder creation wizard."""

from aiogram.fsm.state import State, StatesGroup


class ReminderWizard(StatesGroup):
    entering_text = State()
    choosing_time = State()
    # NOTE: 'confirming' was removed — it was declared but never transitioned
    # into by any handler.  A live user in that state would have been stuck
    # forever with no escape route (only /start would bail them out).
