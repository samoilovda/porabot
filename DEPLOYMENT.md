# Porabot - Ubuntu Server Deployment Guide

## Prerequisites

- Ubuntu 20.04/22.04 LTS server
- Docker and Docker Compose installed
- Git or project files transferred to server

---

## Step 1: Transfer Project to Server

```bash
# Option A: Clone from GitHub
git clone https://github.com/samoilovda/porabot.git /opt/porabot
cd /opt/porabot

# Option B: Copy existing project
rsync -avz --exclude='.env' . user@server:/opt/porabot/
```

---

## Step 2: Create Environment File

Create `/opt/porabot/.env` on the server:

```bash
nano /opt/porabot/.env
```

Add your configuration:

```bash
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_ID=your_admin_user_id_here
ALLOWED_USERS=[admin_id]
TZ=Europe/Moscow  # or your server timezone
DATABASE_URL=sqlite+aiosqlite:////app/data/porabot.db
SCHEDULER_DB_URL=sqlite:////app/data/jobs.sqlite
```

**Important:** Replace `your_telegram_bot_token_here` and `your_admin_user_id_here` with actual values!

**Important:** `DATABASE_URL`/`SCHEDULER_DB_URL` use **four** slashes after the
scheme (`sqlite+aiosqlite:////app/...`), not three — the extra slash is what
makes the path absolute inside the container (`/app/data/...`), matching
where `docker-compose.yml`'s `./data:/app/data` volume is mounted. This is
also what `docker-compose.yml` itself sets via its own `environment:` block,
which overrides `.env` when you run through `docker compose up`. But if you
ever run the image directly (`docker run --env-file .env ...`, no compose)
instead, `.env` is all that's read — a three-slash relative path there would
put the database outside the mounted volume, invisible to
`docker compose up -d --build` and lost on the next container recreate.

---

## Step 3: Create Data Directory

```bash
mkdir -p /opt/porabot/data
chmod 755 /opt/porabot/data
```

---

## Step 4: Build and Run with Docker

```bash
cd /opt/porabot
docker compose up -d --build
```

---

## Step 5: Verify Deployment

```bash
# Check container status
docker ps

# View logs
docker logs porabot --tail=50

# Expected output should show:
# - Database initialized.
# - Scheduler started.
# - Starting polling...
```

---

## Step 6: Monitor the Bot

```bash
# Live logs
docker compose logs -f porabot

# Check resource usage
docker stats porabot
```

---

## Step 7: Restart Commands

```bash
# Graceful restart
docker compose restart porabot

# Stop and rebuild (after code changes)
docker compose down
docker compose up -d --build
```

---

## Troubleshooting

### If container keeps restarting:

```bash
# Check exit code
docker inspect porabot --format='{{.State.ExitCode}}'

# View full error logs
docker logs porabot 2>&1 | grep -i "error\|exception"
```

### Common Issues:

| Issue | Solution |
|-------|----------|
| `sqlite3 database is locked` | Check if another process holds the DB connection |
| `Unauthorized` errors | Verify BOT_TOKEN is valid and not banned |
| Memory issues | Increase server RAM or optimize bot usage |

---

## Optional: Systemd Service Wrapper

For production, create a systemd service for easier management:

```bash
sudo nano /etc/systemd/system/porabot.service
```

Add this content:

```ini
[Unit]
Description=Porabot Telegram Bot
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/porabot
Environment="PATH=/usr/local/bin:/usr/bin"
ExecStart=/usr/bin/docker compose -f /opt/porabot/docker-compose.yml up -d
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable porabot
sudo systemctl start porabot
sudo systemctl status porabot
```

---

## Security Recommendations

1. **Never commit `.env` to Git** - Add it to `.gitignore`
2. **Use strong BOT_TOKEN** from @BotFather
3. **Restrict ALLOWED_USERS** to trusted users only
4. **Regular backups** of `/opt/porabot/data/` directory (see below)

---

## Backups

`data/` holds two SQLite databases with WAL mode enabled — a plain `cp`/`rsync`
of the live file can capture it mid-write and produce a torn, unrecoverable
copy. `scripts/backup.sh` uses SQLite's own online backup API instead
(`sqlite3 <file> ".backup <dest>"`), safe to run against a database the bot
is actively writing to:

```bash
cd /opt/porabot
./scripts/backup.sh              # data/ -> backups/, 7-day retention
./scripts/backup.sh data backups 30   # explicit paths + 30-day retention
```

Requires the `sqlite3` CLI on the host (`apt install sqlite3`). For daily
automated backups, add a cron entry:

```bash
0 3 * * * cd /opt/porabot && ./scripts/backup.sh >> /var/log/porabot-backup.log 2>&1
```

---

## Health Monitoring

`docker-compose.yml` defines a `healthcheck` that fails if `bot/__main__.py`
hasn't updated its heartbeat file in the last 150 seconds — this catches a
polling loop that's hung (deadlocked, stuck on a call that never times out),
which `restart: always` alone can't: it only reacts to the process exiting,
and a hung-but-alive process never does. Check current health with:

```bash
docker inspect --format='{{.State.Health.Status}}' porabot
```

---

## Update Bot After Code Changes

```bash
# Pull latest code (if using git)
git pull origin main

# Rebuild container
docker compose down
docker compose up -d --build
```

---

## Logs Location

- Docker logs: `docker logs porabot`
- Database files: `/opt/porabot/data/`
- Audit log (if enabled): `/opt/porabot/audit_log.txt`

---

**Deployment complete! Your bot should now run stably on Ubuntu server.** 🚀