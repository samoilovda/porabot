import asyncio
from datetime import datetime
import dateparser.search


class InputParser:
    """
    Класс для "умного" парсинга ввода пользователя.
    Выделяет дату и время из текста и очищает текст от них.
    """

    def _normalize_text(self, text: str) -> str:
        """
        Нормализует текст: заменяет разговорные обороты на понятные числа.
        """
        replacements = {
            "полчаса": "30 минут",
            "полтора часа": "1 час 30 минут",
            "через пару минут": "через 2 минуты",
        }
        
        normalized = text.lower()
        for key, value in replacements.items():
            if key in normalized:
                normalized = normalized.replace(key, value)
        
        return normalized

    def _parse_input_sync(self, text: str, timezone: str) -> dict:
        """
        Синхронная версия парсинга (CPU-bound).
        """
        # 1. Нормализация
        normalized_text = self._normalize_text(text)
        
        settings = {
            'PREFER_DATES_FROM': 'future',
            'TIMEZONE': timezone,
            'RETURN_AS_TIMEZONE_AWARE': True
        }
        languages = ['ru', 'en']

        # search_dates возвращает список кортежей (substring, datetime_obj) или None
        matches = dateparser.search.search_dates(
            normalized_text,
            languages=languages,
            settings=settings
        )

        parsed_datetime = None
        clean_text = normalized_text # Работаем с нормализованным

        if matches:
            # Берем первое найденное совпадение.
            matched_substring, dt_obj = matches[0]
            parsed_datetime = dt_obj

            # Удаляем найденную подстроку из текста (нормализованного)
            clean_text = clean_text.replace(matched_substring, "", 1)
            
            # Очистка от лишних пробелов (двойные пробелы, пробелы по краям)
            clean_text = " ".join(clean_text.split())

        return {
            'clean_text': clean_text,
            'parsed_datetime': parsed_datetime
        }

    async def parse_input(self, text: str, timezone: str) -> dict:
        """
        Асинхронная обертка для парсинга.
        Запускает CPU-bound задачу в отдельном потоке, чтобы не блокировать Event Loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_input_sync, text, timezone)
