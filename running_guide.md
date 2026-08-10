# Инструкция по запуску Porabot

Для запуска бота локально выполните следующие команды в терминале:

1. **Переход в папку проекта**:
   ```bash
   cd /Users/den/porabot
   ```

2. **Установка зависимостей**:
   ```bash
   pip install "setuptools<81" wheel
   pip install -r requirements.txt
   ```
   (пин `setuptools<81` обязателен — иначе сборка `docopt`, зависимости
   `natasha`, падает на новых окружениях; см. README.md)

   **Если это не помогло и сборка `docopt` всё равно падает** с ошибкой
   вида `AttributeError: install_layout` — вы на системном Python
   Debian/Ubuntu (`apt install python3`), а не на «чистой» сборке
   (`python.org`, `pyenv`, официальный образ `python:3.11-slim`, который
   использует `Dockerfile`). Debian патчит `distutils`, убирая
   совместимость, на которую опирается старый `setup.py` пакета `docopt` —
   продакшен-сборка через `Dockerfile`/CI этой проблемы не имеет. Обходной
   путь для локальной разработки:
   ```bash
   pip install "setuptools<81" wheel docopt-ng
   pip install --no-deps -r requirements.lock
   pip install pytest pytest-asyncio
   ```
   (`docopt-ng` — обратно совместимый форк с современным `pyproject.toml`;
   `--no-deps` пропускает попытку собрать `docopt` из `requirements.lock`
   отдельно, раз он уже удовлетворён форком).

3. **Запуск**:
   ```bash
   python3 -m bot
   ```

---
**Заметка**: Бот использует файл `.env` для конфигурации. Убедитесь, что `BOT_TOKEN` там указан верно.
Для остановки бота нажмите `Ctrl+C`.
