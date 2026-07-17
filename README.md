# Porabot

A personal Telegram bot for scheduling reminders and tracking habits. The defining feature: create a task in one natural-language phrase in **Russian, English, or Spanish** — no wizard, no menu, just send the text and the bot works out when you mean.

> **TODO (owner):** add GIF of one-phrase task creation, habit completion, and weekly report here.

Also available in: [Русский](README.ru.md) · [Español](README.es.md)

---

## Features

### Natural-language task creation

Type a phrase; the bot parses time and extracts the task description:

| Input | Result |
|---|---|
| `вечером принять лекарство` | reminder at 19:00 today — "принять лекарство" |
| `завтра в 10 утра позвонить маме` | tomorrow 10:00 — "позвонить маме" |
| `in 15 minutes check the oven` | 15 minutes from now — "check the oven" |
| `mañana a las 9 tomar vitaminas` | tomorrow 09:00 — "tomar vitaminas" |
| `каждый день в 9 тренировка` | daily recurring at 09:00 — "тренировка" |

### Reminders

- **One-time and recurring** — daily, weekly, or any `RRULE`-expressible pattern
- **Nagging mode** — follow-up every 5 minutes, capped at a per-task repeat limit (default 3)
- **Snooze** — 15 min / 1 hour / tomorrow
- **Media attachments** — photo or video forwarded together with the reminder text
- **Quiet hours** — configurable sleep window that suppresses all notifications
- **Missed-task recovery** — after a bot restart a catch-up digest surfaces missed reminders with one-tap "done all" or "snooze all"

### Habits

- **Fixed-time mode** — habit fires at a specific clock time every day; streak counted per scheduled `due_at` timestamp
- **Fluid mode** — habit is due any time within a local calendar day; streak is day-based
- **Presets** — common habit templates selectable at creation
- **Streaks** — `habit_streak_current` / `habit_streak_best` maintained per habit; fluid habits track `fluid_streak_current` / `fluid_streak_best` separately
- **Undo** — reverts the last completion and restores the streak to the value it had before (via `habit_undo_pending` flag)
- **"Not today"** — logs the cycle as `not_today` without breaking the streak
- **Habit sweeper** — background minutely job detects cycles missed while the bot was down or the user had notifications off; writes `missed` events from live DB state, independent of whether any notification was ever sent
- **Weekly and monthly reports** — aggregated done / not-today / missed rates per habit, sent at a user-configured weekday and time

### General

- **Three languages** — full i18n in Russian, English, Spanish (`bot/lexicon/ru.py`, `en.py`, `es.py`); natural-language time parsing works in all three
- **Timezone-aware** — all times stored in UTC; displayed in user's local timezone; DST transitions handled correctly
- **Morning and evening briefs** — daily summary at configurable times
- **Per-user settings** — timezone, language, quiet hours, nagging limit, reports schedule

---

## Engineering decisions worth noting

1. **Everything in UTC, converted on display.** `execution_time` is stored as naive UTC in SQLite. Conversion to local wall-clock time happens only when rendering messages. The scheduler's `reconcile_jobs_with_db` recomputes the next RRULE occurrence in the user's local timezone so a bot restart across a DST transition does not shift the clock time of daily reminders. DST correctness is covered by dedicated tests (`test_dst_recurring_reminder.py`, `test_reconcile_dst_next_occurrence.py`).

2. **Persistent APScheduler with startup reconciliation.** Jobs survive restarts via `SQLAlchemyJobStore`. On boot, `reconcile_jobs_with_db` scans every `pending` reminder: if its job is absent from the store (overdue after downtime) it schedules a catch-up job within 1 minute. `last_fired_at` prevents re-delivering notifications the user already received.

3. **Idempotent habit-event log.** `HabitEvent` has a `UNIQUE(reminder_id, cycle_key)` constraint. `cycle_key` is `due:<unix_ts>` for fixed habits and `day:<YYYY-MM-DD>` for fluid ones. `HabitEventDAO.record()` checks for an existing row first; on concurrent-tap races it uses `SAVEPOINT` (`begin_nested`) instead of a session-level `rollback()` — rolling back the whole session would have silently discarded all other pending changes in the same Unit of Work.

4. **`forbidden_strikes` against infinite retry.** Every `TelegramForbiddenError` (user blocked the bot) increments `forbidden_strikes`. Once it reaches `FORBIDDEN_STRIKES_LIMIT = 3`, the reminder is excluded from reconciliation and nagging stops — the bot does not hammer a delivery address that will never accept messages.

5. **Habit sweeper decoupled from notification delivery.** Missed cycles are detected by `habit_sweeper.py` from live DB state every minute, not by hooking into the moment the notification fires. A missed cycle is recorded even if the bot was down, the user had blocked it, or nagging was disabled.

6. **DAO layer + composition root.** All DB access goes through typed DAO classes (`ReminderDAO`, `UserDAO`, `HabitEventDAO`). `bot/__main__.py` is the composition root: it wires infrastructure (DB engine, scheduler, bot, session factory) but contains zero business logic.

7. **NLP parser offloaded to thread pool.** Natasha's `DatesExtractor` is not reentrant; a module-level `threading.Lock` serialises all Natasha calls. The synchronous parse pipeline runs via `loop.run_in_executor` so the asyncio event loop is never blocked.

---

## Test coverage

```
135 tests collected   (python -m pytest --collect-only -q)
```

Tests follow the regression style: each test was written to fail on the specific bug it guards, then the fix was applied. Test files live alongside source in `bot/services/`.

CI (`deploy.yml`) runs the full suite on every push to `main` and gates deployment — the VPS is only updated if all 135 tests pass.

---

## Process artifacts

Three formal self-audit rounds are part of this repository's history and are kept visible on purpose — they are evidence of the development process:

- [`AUDIT.md`](AUDIT.md) — audit of commit `ea1ced1`; 17 findings (C1–C4, W1–W8, N1–N7), priority-tagged P0/P1/P2, with reproduction commands and fix status
- [`REWORK_PLAN.md`](REWORK_PLAN.md) — phase-1 rework plan generated from the audit
- [`REWORK_PLAN_2.md`](REWORK_PLAN_2.md) — phase-2 plan covering habit statistics, sweeper, and report features

---

## Setup and run

### Docker Compose (recommended)

```bash
cp .env.example .env
# fill in BOT_TOKEN and ADMIN_ID
docker compose up -d
```

SQLite databases are persisted in `./data/`.

### Local run

```bash
python3 -m venv .venv && source .venv/bin/activate

# natasha → yargy → pymorphy2 → docopt chain fails against setuptools ≥ 81
pip install "setuptools<81" wheel
pip install -r requirements.txt

python -m bot
```

For a fully reproducible install use the pinned lock file:

```bash
pip install "setuptools<81" wheel
pip install -r requirements.lock
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram Bot API token |
| `ADMIN_ID` | ✅ | Your Telegram user ID |
| `ALLOWED_USERS` | — | JSON array of whitelisted user IDs |
| `TZ` | — | Default timezone (e.g. `Europe/Moscow`) |
| `DATABASE_URL` | — | SQLite URL; default `sqlite+aiosqlite:///porabot.db` |
| `SCHEDULER_DB_URL` | — | APScheduler jobstore URL; default `sqlite:///jobs.sqlite` |

---

## Known limitations

- **O(users) minutely jobs.** Four cron jobs (daily briefs, missed recovery, habit sweeper, habit reports) scan all users every minute. Intentional and acceptable for a personal bot; does not scale to thousands of users without additional indexing. Tracked as W4 in `AUDIT.md`.
- **Timezone input is whole-hours only.** The onboarding flow accepts integer UTC offsets (e.g. `+3`). Half-hour and quarter-hour offsets (India, Nepal, Iran, etc.) require manual `.env` configuration. Tracked as W8 in `AUDIT.md`.
- **Whitelist is implemented but disabled by default.** `WhitelistMiddleware` exists in `bot/middlewares/whitelist.py`. Enable it in `bot/__main__.py` before deploying publicly.

---

## Origin

Built with AI-assisted development — the owner (a clinical psychologist) acts as operator and product designer; code is written in pair with AI agents under the owner's direction.
