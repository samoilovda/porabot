# Porabot — agent instructions

Telegram bot (aiogram 3, SQLite, APScheduler): reminders and habits, tasks created from one natural-language phrase in ru/en/es/pt. Live bot with real users. **Merging to `main` deploys to production.**

## Code map
- `bot/__main__.py` — composition root (config → DB → middlewares → routers → scheduler → polling, heartbeat). No business logic.
- `bot/config.py` — pydantic `Settings`, validated at import; `BOT_TOKEN` is required.
- `bot/database/` — `models.py`, `engine.py` (engine, `init_db`, soft migrations), `dao/` (all DB access goes through DAOs).
- `bot/handlers/` — routers (`reminders_*.py`, `habits.py`, `commands.py`, `experience.py`, `settings/`, ...).
- `bot/keyboards/`, `bot/states/` (FSM), `bot/middlewares/` (rate limit, private-chat-only, DB session).
- `bot/services/` — scheduler, NL `parser.py`, briefs, reports, recovery, cleanup, `webserver.py` (ICS feed + Mini App API), `ui_context.py`.
- `bot/lexicon/` — i18n dictionaries `en.py` (reference), `ru.py`, `es.py`, `pt.py`.
- `bot/utils/` — time, timezone, markdown, pagination, tags helpers.
- `bot/webapp/` — Telegram Mini App static frontend (`index.html`, `app.js`, `style.css`); see `bot/webapp/AGENTS.md`.
- `tests/` — pytest (`services/`, `keyboards/`, `utils/`); `tests/frontend/` — Node tests for the Mini App.
- `scripts/` — `backup.sh`, `verify_backup.sh`, `scan_secrets.py`. `docs/` — audits and plans.

## Documents
Current:
- `DEPLOYMENT.md` — server env, backups, optional web server/Mini App, healthcheck. Its "Update Bot After Code Changes" section is superseded by `.github/workflows/deploy.yml`.
- `README.md` — product features and architecture notes (English; `README.ru.md`, `README.es.md` are translations).
- `running_guide.md` — local install, incl. the Debian `docopt` workaround (Russian; its `cd` path is the author's, ignore it).

Descriptive snapshots, may be stale: `BOT_OVERVIEW.md` (dated 2026-07-16), `ROADMAP.md` (marked outdated in its header).

Historical — do not read unless the task names them: `docs/audits/*` (dated audits, `REWORK_PLAN_*`), `docs/plans/*` (executed plans).

Precedence when sources disagree: code and tests > `.github/workflows/deploy.yml` > `DEPLOYMENT.md` > `README.md` > snapshots.

## Commands
Python 3.11 (CI and Dockerfile); other versions are not guaranteed.
- Setup: `pip install "setuptools<81" wheel && pip install -r requirements.lock pytest pytest-asyncio ruff`. The `setuptools<81` pin is mandatory (natasha → pymorphy2 needs `pkg_resources`). If `docopt` fails to build on Debian/Ubuntu system Python, see `running_guide.md`.
- Tests: `python -m pytest -q` (`conftest.py` sets a dummy `BOT_TOKEN` and in-memory DB).
- Lint: `ruff check .` (no Python formatter).
- Secrets scan: `python3 scripts/scan_secrets.py --no-history`.
- Mini App: `npm ci && npm test`. Needs Chrome/Chromium: `CHROMIUM_PATH`, else `/usr/bin/chromium` or `/usr/bin/google-chrome`. macOS: `CHROMIUM_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" npm test`.
- Before a PR run all four; CI runs the same.
- Run locally: `python -m bot` with `.env` per `.env.example`, using a separate test bot token from @BotFather and your own `ADMIN_ID`, never the production token. Not run: no test token available. It writes `porabot.db`, `jobs.sqlite`, `heartbeat` to the CWD.

## Deploy
- Push to `main` runs the `test` job, then the `deploy` job (SSH to the VPS). PRs run `test` only.
- Deploy checks out the exact tested SHA, runs `scripts/backup.sh` and `scripts/verify_backup.sh` (both block on failure), then `docker compose up -d --build porabot_bot` using the orchestrator's compose file, not this repo's `docker-compose.yml`. Rollback to the previous SHA if the container is unhealthy.
- Host, paths and keys live in GitHub secrets. Do not take host names from old commit messages or docs.
- Never merge a PR yourself; open it and stop. Never push to `main`.

## Working rules
- Commit style (see `git log`): `type: lowercase imperative summary`, types `feat`, `fix`, `docs`, `ci`, `ops`, `test`, `chore`, `refactor`, `ux`.
- Work on a branch `type/short-name` (`fix/`, `feat/`, `ux/`, `ops/`, `docs/`, `chore/`), one logical change per PR, PR into `main`.
- Write a failing test first for a bug fix.
- No Alembic. Schema changes go in `bot/database/models.py`; `init_db` runs `create_all` plus `_add_column_if_missing`/`_add_index_if_missing` for every model column at each startup. A new column must be nullable or have a server default (SQLite rejects `ADD COLUMN NOT NULL` without a default, and the failed ALTER aborts startup). Data backfills go through `_run_once` in `engine.py`. Cover them in `tests/services/test_engine_soft_migrations.py`-style tests. Renames, drops and type changes are NOT handled automatically: ask first.
- i18n: every user-visible string lives in `bot/lexicon/`. Add each key to all four of `en.py`, `ru.py`, `es.py`, `pt.py` in the same change; `tests/services/test_l10n_key_coverage.py` requires every `en` key in the others. Code indexes `l10n["key"]` directly, so a missing key is a runtime `KeyError`. Unknown or missing user language falls back to `ru` (`DEFAULT_LANG`).
- Mini App strings are separate from the lexicon (see `bot/webapp/AGENTS.md`).
- User-visible behaviour change: update lexicons and the feature list in `README.md`. No CHANGELOG exists. Keep `README.ru.md` / `README.es.md` consistent with `README.md` when you touch a shared section.
- Keep DB access in `bot/database/dao/`; keep `bot/__main__.py` free of business logic.
- Do not add dependencies without updating both `requirements.txt` and the pinned `requirements.lock` (the Dockerfile installs the lock).

## Never touch / never commit
- Never open, print or edit `.env`; read `.env.example` only. Never put a token, key or user ID list in code, tests, docs or commit messages.
- Never commit: `*.db`, `*.sqlite`, `*.db-wal/-shm`, `data/`, `backups/`, `heartbeat`, `audit_log.txt`, `.env*` other than `.env.example`, `node_modules/`, caches, `.DS_Store`, `.claude/`.
- Never copy, read or send production data or backups.
- Stage files by explicit path, never `git add -A`/`.`.
- Do not touch unrelated uncommitted changes in the working tree.
