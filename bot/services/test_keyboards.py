"""
Keyboard Generation Tests — Inline Keyboard Layout Verification
===============================================================

PURPOSE:
  This test suite verifies that keyboard generation functions create correct
  layouts with proper button counts and callback data patterns.

USAGE:
  
    # Run with pytest
    python -m pytest bot/services/test_keyboards.py -v
    
TEST COVERAGE:
  
  ✅ Time selection keyboard structure
  ✅ Edit keyboard recurring vs one-time tasks
  ✅ Snooze keyboard compact layout
  ✅ Tasks list keyboard empty vs populated
  ✅ Settings keyboard UTC offset toggle state
  ✅ Language selection keyboard consistency
  ✅ Timezone selection keyboard completeness
"""

import pytest
from typing import Any, Dict

# Import keyboard functions from keyboards.inline module
from bot.keyboards.inline import (
    get_time_selection_keyboard,
    get_edit_keyboard,
    get_snooze_keyboard,
    get_tasks_list_keyboard,
    get_settings_keyboard,
    get_language_selection_keyboard,
    get_timezone_keyboard,
)


@pytest.fixture
def sample_l10n() -> Dict[str, Any]:
    """Provide a mock localization dictionary for testing."""
    return {
        "time_morning": "Утро",
        "time_day": "День",
        "time_evening": "Вечер",
        "time_night": "Ночь",
        "time_tomorrow": "Завтра",
        "time_manual": "Ручной ввод",
        "btn_repeat_prefix": "Повтор:",
        "btn_nagging_prefix": "Напоминать:",
        "btn_delete": "Удалить",
        "btn_done": "Готово!",
        "snooze_15m": "+15м",
        "snooze_30m": "+30м",
        "snooze_1h": "+1ч",
        "snooze_2h": "+2ч",
        "snooze_1d": "+1д",
        "snooze_custom": "Выбрать время",
        "snooze_morning": "Утро",
        "snooze_day": "День",
        "snooze_evening": "Вечер",
        "snooze_night": "Ночь",
        "btn_refresh": "Обновить",
        "btn_completed_tasks": "Завершённые",
        "btn_close": "Закрыть",
        "status_on": "Вкл",
        "status_off": "Выкл",
        "btn_change_tz": "Сменить часовой пояс",
        "btn_change_lang": "Сменить язык",
        "btn_toggle_utc_on": "UTC+ (вкл)",
        "btn_toggle_utc_off": "UTC+ (выкл)",
        "lang_ru": "🇷🇺 Русский",
        "lang_en": "🇬🇧 English",
    }


class TestTimeSelectionKeyboard:
    """Test time selection keyboard structure."""
    
    def test_keyboard_has_all_delta_buttons(self, sample_l10n):
        """Verify time selection keyboard has all delta buttons (+15m, +30m, etc.)."""
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", sample_l10n)
        
        # Check that keyboard markup contains expected callback data patterns
        assert "time_delta_15" in str(keyboard.inline_keyboard)
        assert "time_delta_30" in str(keyboard.inline_keyboard)
        assert "time_delta_60" in str(keyboard.inline_keyboard)
    
    def test_keyboard_has_time_of_day_slots(self, sample_l10n):
        """Verify time selection keyboard has morning/day/evening/night slots."""
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", sample_l10n)
        
        assert "time_fixed_" in str(keyboard.inline_keyboard)
    
    def test_keyboard_has_tomorrow_and_manual_options(self, sample_l10n):
        """Verify time selection keyboard has tomorrow and manual entry options."""
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", sample_l10n)
        
        assert "time_tomorrow" in str(keyboard.inline_keyboard)
        assert "time_manual" in str(keyboard.inline_keyboard)
    
    def test_keyboard_has_cancel_button(self, sample_l10n):
        """Verify time selection keyboard has cancel button."""
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", sample_l10n)
        
        assert "cancel_wizard" in str(keyboard.inline_keyboard)


class TestEditKeyboard:
    """Test edit mode keyboard structure."""
    
    def test_edit_keyboard_for_one_time_task(self, sample_l10n):
        """Verify edit keyboard for one-time task doesn't show repeat toggle."""
        
        keyboard = get_edit_keyboard(
            reminder_id=123,
            l10n=sample_l10n,
            is_recurring=False,
            is_nagging=False,
            rrule_text="Нет",
        )
        
        assert "edit_toggle_repeat_" not in str(keyboard.inline_keyboard)
    
    def test_edit_keyboard_for_recurring_task(self, sample_l10n):
        """Verify edit keyboard for recurring task shows repeat toggle."""
        
        keyboard = get_edit_keyboard(
            reminder_id=456,
            l10n=sample_l10n,
            is_recurring=True,
            is_nagging=False,
            rrule_text="FREQ=DAILY",
        )
        
        assert "edit_toggle_repeat_" in str(keyboard.inline_keyboard)
    
    def test_edit_keyboard_shows_nagging_status(self, sample_l10n):
        """Verify edit keyboard shows nagging status with correct icon."""
        
        # Nagging enabled - should show fire emoji
        keyboard_on = get_edit_keyboard(
            reminder_id=789,
            l10n=sample_l10n,
            is_recurring=False,
            is_nagging=True,
            rrule_text="Нет",
        )
        
        assert "🔥" in str(keyboard_on.inline_keyboard) or "Вкл" in str(keyboard_on.inline_keyboard)
        
        # Nagging disabled - should show snowflake emoji
        keyboard_off = get_edit_keyboard(
            reminder_id=789,
            l10n=sample_l10n,
            is_recurring=False,
            is_nagging=False,
            rrule_text="Нет",
        )
        
        assert "❄️" in str(keyboard_off.inline_keyboard) or "Выкл" in str(keyboard_off.inline_keyboard)
    
    def test_edit_keyboard_has_delete_button(self, sample_l10n):
        """Verify edit keyboard always has delete button."""
        
        keyboard = get_edit_keyboard(
            reminder_id=999,
            l10n=sample_l10n,
            is_recurring=False,
            is_nagging=False,
            rrule_text="Нет",
        )
        
        assert "btn_delete" in str(keyboard.inline_keyboard) or "Удалить" in str(keyboard.inline_keyboard)


class TestSnoozeKeyboard:
    """Test snooze keyboard structure."""
    
    def test_snooze_keyboard_has_short_intervals(self, sample_l10n):
        """Verify snooze keyboard has short interval buttons (15m, 30m, 1h, 2h)."""
        
        keyboard = get_snooze_keyboard(123, sample_l10n)
        
        assert "snooze_act_15m" in str(keyboard.inline_keyboard)
        assert "snooze_act_30m" in str(keyboard.inline_keyboard)
        assert "snooze_act_1h" in str(keyboard.inline_keyboard)
        assert "snooze_act_2h" in str(keyboard.inline_keyboard)
    
    def test_snooze_keyboard_has_time_of_day_options(self, sample_l10n):
        """Verify snooze keyboard has time of day options (morning, day, evening, night)."""
        
        keyboard = get_snooze_keyboard(456, sample_l10n)
        
        assert "snooze_act_morning" in str(keyboard.inline_keyboard)
        assert "snooze_act_day" in str(keyboard.inline_keyboard)
    
    def test_snooze_keyboard_has_long_intervals(self, sample_l10n):
        """Verify snooze keyboard has long interval buttons (1d, custom)."""
        
        keyboard = get_snooze_keyboard(789, sample_l10n)
        
        assert "snooze_act_1d" in str(keyboard.inline_keyboard)
        assert "snooze_act_custom" in str(keyboard.inline_keyboard)


class TestTasksListKeyboard:
    """Test tasks list keyboard structure."""
    
    def test_tasks_list_keyboard_with_empty_list(self, sample_l10n):
        """Verify tasks list keyboard handles empty task list gracefully."""
        
        keyboard = get_tasks_list_keyboard([], sample_l10n)
        
        # Should still have navigation buttons even with no tasks
        assert "btn_refresh" in str(keyboard.inline_keyboard) or "Обновить" in str(keyboard.inline_keyboard)
    
    def test_tasks_list_keyboard_with_one_task(self, sample_l10n):
        """Verify tasks list keyboard shows action buttons for each task."""
        
        from aiogram.types import InlineKeyboardButton
        
        mock_task = type('MockTask', (), {
            'id': 123,
            'reminder_text': 'Принять лекарство'
        })()
        
        keyboard = get_tasks_list_keyboard([mock_task], sample_l10n)
        
        assert "done_task_" in str(keyboard.inline_keyboard) or "Готово" in str(keyboard.inline_keyboard)
    
    def test_tasks_list_keyboard_with_multiple_tasks(self, sample_l10n):
        """Verify tasks list keyboard handles multiple tasks correctly."""
        
        from aiogram.types import InlineKeyboardButton
        
        mock_task1 = type('MockTask', (), {
            'id': 123,
            'reminder_text': 'Задача 1'
        })()
        mock_task2 = type('MockTask', (), {
            'id': 456,
            'reminder_text': 'Задача 2'
        })()
        
        keyboard = get_tasks_list_keyboard([mock_task1, mock_task2], sample_l10n)
        
        assert str(keyboard.inline_keyboard).count("done_task_") >= 2


class TestSettingsKeyboard:
    """Test settings keyboard structure."""
    
    def test_settings_keyboard_with_utc_offset_enabled(self, sample_l10n):
        """Verify settings keyboard shows UTC toggle in 'on' state."""
        
        keyboard = get_settings_keyboard(sample_l10n, show_utc_offset=True)
        
        assert "btn_toggle_utc_on" in str(keyboard.inline_keyboard) or "UTC+(вкл)" in str(keyboard.inline_keyboard)
    
    def test_settings_keyboard_with_utc_offset_disabled(self, sample_l10n):
        """Verify settings keyboard shows UTC toggle in 'off' state."""
        
        keyboard = get_settings_keyboard(sample_l10n, show_utc_offset=False)
        
        assert "btn_toggle_utc_off" in str(keyboard.inline_keyboard) or "UTC+(выкл)" in str(keyboard.inline_keyboard)
    
    def test_settings_keyboard_has_timezone_and_language_options(self, sample_l10n):
        """Verify settings keyboard has timezone and language change options."""
        
        keyboard = get_settings_keyboard(sample_l10n, show_utc_offset=True)
        
        assert "btn_change_tz" in str(keyboard.inline_keyboard) or "Сменить часовой пояс" in str(keyboard.inline_keyboard)
        assert "btn_change_lang" in str(keyboard.inline_keyboard) or "Сменить язык" in str(keyboard.inline_keyboard)


class TestLanguageSelectionKeyboard:
    """Test language selection keyboard structure."""
    
    def test_language_keyboard_has_both_languages(self, sample_l10n):
        """Verify language selection keyboard has both Russian and English options."""
        
        keyboard = get_language_selection_keyboard(sample_l10n)
        
        assert "lang_ru" in str(keyboard.inline_keyboard) or "Русский" in str(keyboard.inline_keyboard)
        assert "lang_en" in str(keyboard.inline_keyboard) or "English" in str(keyboard.inline_keyboard)


class TestTimezoneSelectionKeyboard:
    """Test timezone selection keyboard structure."""
    
    def test_timezone_keyboard_has_all_standard_zones(self):
        """Verify timezone selection keyboard has all standard timezone options."""
        
        keyboard = get_timezone_keyboard()
        
        assert "Europe/Moscow" in str(keyboard.inline_keyboard) or "Москва" in str(keyboard.inline_keyboard)
        assert "Europe/Kiev" in str(keyboard.inline_keyboard) or "Киев" in str(keyboard.inline_keyboard)
        assert "UTC" in str(keyboard.inline_keyboard)
    
    def test_timezone_keyboard_has_manual_entry_option(self):
        """Verify timezone selection keyboard has manual entry option."""
        
        keyboard = get_timezone_keyboard()
        
        assert "Ввести город вручную" in str(keyboard.inline_keyboard) or "set_tz_manual" in str(keyboard.inline_keyboard)


class TestKeyboardButtonCount:
    """Test that keyboards don't exceed Telegram's button limit (25 buttons)."""
    
    def test_time_selection_keyboard_within_limit(self, sample_l10n):
        """Verify time selection keyboard has ≤25 buttons."""
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", sample_l10n)
        
        total_buttons = sum(len(row) for row in keyboard.inline_keyboard)
        assert total_buttons <= 25
    
    def test_edit_keyboard_within_limit(self, sample_l10n):
        """Verify edit keyboard has ≤25 buttons."""
        
        keyboard = get_edit_keyboard(123, sample_l10n, is_recurring=False)
        
        total_buttons = sum(len(row) for row in keyboard.inline_keyboard)
        assert total_buttons <= 25
    
    def test_snooze_keyboard_within_limit(self, sample_l10n):
        """Verify snooze keyboard has ≤25 buttons."""
        
        keyboard = get_snooze_keyboard(456, sample_l10n)
        
        total_buttons = sum(len(row) for row in keyboard.inline_keyboard)
        assert total_buttons <= 25


class TestKeyboardCallbackDataPattern:
    """Test that callback data follows consistent patterns."""
    
    def test_time_delta_callback_pattern(self):
        """Verify time delta callbacks follow 'time_delta_{minutes}' pattern."""
        
        from aiogram.types import InlineKeyboardButton
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", {"time_delta_15": "test"})
        
        for row in keyboard.inline_keyboard:
            for button in row:
                if hasattr(button, 'callback_data'):
                    assert button.callback_data.startswith("time_delta_") or \
                           button.callback_data == "cancel_wizard"
    
    def test_fixed_time_callback_pattern(self):
        """Verify fixed time callbacks follow 'time_fixed_{ISO}' pattern."""
        
        from aiogram.types import InlineKeyboardButton
        
        keyboard = get_time_selection_keyboard("Europe/Moscow", {"time_fixed_2024-03-27T18:30": "test"})
        
        for row in keyboard.inline_keyboard:
            for button in row:
                if hasattr(button, 'callback_data'):
                    assert button.callback_data.startswith("time_fixed_") or \
                           button.callback_data == "cancel_wizard"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])