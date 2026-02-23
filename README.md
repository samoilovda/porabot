# Porabot - Telegram Reminder Bot

Porabot is a smart reminder bot for Telegram with a "nagging" mode that keeps bothering you until you get things done.

## Features

- **Smart Parsing**: Understands "tomorrow at 9", "in 15 mins", loop/repeat patterns.
- **Main Menu**: Easy access to "New Task", "My Tasks", and "Settings".
- **Task Management**: View and delete your active tasks directly from the bot.
- **Timezone Awareness**: Handles user timezones correctly.
- **Nagging Mode**: Keeps reminding you every 5 minutes until you mark the task as done.
- **Recurring Tasks**: Supports daily, weekly, and custom repetition.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *(Dependencies: `aiogram`, `sqlalchemy`, `aiosqlite`, `apscheduler`, `dateparser`, `python-dateutil`, `pytz`)*

2.  **Set Environment Variables**:
    Create a `.env` file or export the variable:
    ```bash
    export BOT_TOKEN="your_telegram_bot_token"
    ```

3.  **Run the Bot**:
    ```bash
    python3 main.py
    ```

## Usage

1.  Start the bot with `/start`.
2.  Use the **Main Menu**:
    - **➕ New Task**: Start the creation wizard.
    - **📅 My Tasks**: List active tasks and remove them.
    - **⚙️ Settings**: Change your timezone.
3.  You can also just send a text message:
    - "Buy milk tomorrow at 18:00"
    - "Meeting in 2 hours"
4.  Confirm the task and choose options (Repeat, Nagging).
