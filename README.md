# Porabot — Telegram Reminder Bot

A smart Telegram bot for managing scheduled reminders and tasks with intelligent time parsing, recurring task support, nagging mode, and timezone awareness.

## 🌟 Features

| Feature | Description |
|---------|-------------|
| **Smart NLP Parsing** | Understands natural language like "вечером", "завтра в 10 утра", "in 15 minutes" (Russian & English) |
| **One-Time Tasks** | Create tasks that fire once at a specific time |
| **Recurring Tasks** | Daily, weekly, or custom repetition patterns |
| **Nagging Mode** | Bot sends follow-up notifications every 5 min until task is completed |
| **Timezone Awareness** | All execution times stored in UTC but displayed in user's local timezone |
| **Daily Briefs** | Morning (09:00) and evening (23:00) summary messages |
| **Task Management** | View, edit, snooze, mark as done, or delete tasks via menu or text commands |

## 🏗️ Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   User      │────▶│ Handlers     │────▶│ Database +       │
│   Input     │     │ (FSM Wizard) │     │ Scheduler        │
└─────────────┘     └──────────────┘     └─────────────────┘
```

### Layer Structure:

| Layer | Purpose | Key Files |
|-------|---------|-----------|
| **Entry Point** | Application composition root, wiring all layers together | `bot/__main__.py` |
| **Database** | SQLAlchemy ORM models + DAO abstraction for CRUD operations | `bot/database/` (engine.py, models.py, dao/) |
| **Services** | Business logic: NLP parsing, job scheduling, daily briefs | `bot/services/` (parser.py, scheduler.py, daily_briefs.py) |
| **Handlers** | FSM wizard for reminder creation/editing workflow | `bot/handlers/` (reminders.py, etc.) |
| **Middlewares** | Access control + dependency injection | `bot/middlewares/` (whitelist.py, database.py) |

## 📦 Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Dependencies:**
- `aiogram` — Async Telegram Bot API framework
- `sqlalchemy` + `aiosqlite` — Async ORM database layer
- `apscheduler` — Job scheduling for recurring tasks
- `dateparser` — NLP datetime resolution
- `natasha` — Russian language NER (Named Entity Recognition)
- `pytz` — Timezone handling

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_ID=123456789
ALLOWED_USERS=[123456789,987654321]
TZ=Europe/Moscow
DATABASE_URL=sqlite+aiosqlite:///porabot.db
SCHEDULER_DB_URL=sqlite:///jobs.sqlite
```

**Environment Variables:**

| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram Bot API token (required) | `8533364479:AAEGjnW8HXr9DJOAmIL82u6F3UbbJ9ibAJY` |
| `ADMIN_ID` | Your Telegram user ID for admin privileges | `123456789` |
| `ALLOWED_USERS` | List of whitelisted user IDs (comma-separated or JSON array) | `[123, 456]` |
| `TZ` | Default timezone for the bot | `Europe/Moscow`, `UTC`, `America/New_York` |
| `DATABASE_URL` | SQLite connection string for task data | `sqlite+aiosqlite:///porabot.db` |
| `SCHEDULER_DB_URL` | SQLite connection for APScheduler jobs persistence | `sqlite:///jobs.sqlite` |

### 3. Run the Bot

```bash
python -m bot
```

Or directly:

```bash
python bot/__main__.py
```

The bot will start with:
- Database tables initialized
- Scheduler registered for recurring tasks
- Telegram polling active

## 💬 Usage

### Creating Reminders (Text Input)

Simply send a message to the bot describing your task and time:

| Example | What It Creates |
|---------|-----------------|
| `вечером принять лекарство` | One-time reminder for 19:00 today: "принять лекарство" |
| `завтра в 10 утра позвонить маме` | One-time reminder for tomorrow at 10:00: "позвонить маме" |
| `каждый день в 9 часов тренировка` | Daily recurring task at 09:00: "тренировка" |
| `через час напомнить о документе` | One-time reminder in 1 hour: "напомнить о документе" |

### Using the Menu

Click the menu button (or use `/menu`) to access:

- **➕ New Task** — Start the creation wizard with options for repeat/nagging
- **📅 My Tasks** — View all pending tasks, edit or delete them
- **⏰ Snooze** — Delay task execution by 15 min / 1 hour / tomorrow
- **✅ Mark Done** — Complete a task manually
- **⚙️ Settings** — Change timezone, toggle nagging mode

### Editing Tasks

From the task list menu:
- Change execution time
- Toggle recurring (daily/weekly)
- Enable/disable nagging mode
- Add media attachments (photos/videos for context)

### Task Status Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  PENDING    │────▶│ EXECUTED    │────▶│ COMPLETED   │
│ (waiting)   │     │ (notification sent) │ (marked done) │
└─────────────┘     └─────────────┘     └─────────────┘

For recurring tasks, "COMPLETED" means snoozed back to PENDING.
```

## 🧠 Technical Details

### Time Parsing Pipeline

The bot uses a two-stage NLP approach:

1. **Heuristic Normalization** — Converts common phrases like "вечером" → "в 19:00"
2. **Natasha NER + dateparser** — Russian morphological analysis + datetime resolution

### Database Schema

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `users` | User profile data | id, username, timezone, show_utc_offset |
| `reminders` | Task definitions | user_id, reminder_text, execution_time, status, is_recurring, rrule_string, is_nagging, completed_at |

### Scheduler Jobs

- **Recurring Tasks** — APScheduler manages job persistence in separate SQLite DB
- **Daily Briefs** — Hourly cron jobs send morning (09:00) and evening (23:00) summaries
- **Nagging Mode** — Follow-up notifications every 5 minutes until task completion

## 🐛 Bug Fixes Applied (Phase 1)

This release includes several critical fixes from Phase 1 development:

| Issue | Fix Applied |
|-------|-------------|
| **Daily Briefs Global State** | Fixed scheduler job initialization with proper dependency injection |
| **Scheduler Timezone Handling** | All execution times stored in UTC, displayed in user's local timezone |
| **Nagging Mode Issues** | Proper interval calculation and job rescheduling |
| **Session Flush Errors** | Removed double-commit errors in edit handlers |
| **Idempotency Guards** | Prevent rapid double-taps on critical operations (create/edit/delete) |

## 📝 Test Messages to Try

```bash
# Start the bot first
/start

# Then try these:
"вечером принять лекарство"              # Evening medication reminder
"завтра в 10 утра позвонить маме"       # Tomorrow at 10am call mom
"каждый день в 9 часов тренировка"        # Daily workout at 9am
"через час напомнить о документе"         # In 1 hour remind about document
```

## 🐳 Docker Deployment

For containerized deployment:

```bash
docker-compose up -d
```

See `Dockerfile` and `docker-compose.yml` for configuration options.

## 🔧 Development Notes

- **Entry Point Pattern**: `bot/__main__.py` uses composition root architecture — no business logic, only infrastructure wiring
- **DAO Abstraction**: Database operations go through DAO layer (e.g., `ReminderDAO`) for clean separation of concerns
- **Middleware Chain**: Whitelist middleware runs before database middleware to avoid DB overhead for unauthorized users

## 📄 License

Porabot is provided as-is for personal use.

---

**Need Help?** Check `running_guide.md` for additional operational documentation.