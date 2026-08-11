#!/usr/bin/env bash
# Back up Porabot's SQLite databases (REWORK_PLAN_3 4.4).
#
# Uses `sqlite3 <file> ".backup <dest>"`, NOT a plain file copy: WAL mode
# is enabled (see bot/database/engine.py's create_engine) so the live
# database file on disk can be mid-write at any moment — `cp`/`rsync`-ing it
# directly can capture a torn, unrecoverable snapshot. `.backup` uses
# SQLite's own online backup API, safe to run against a database the bot is
# actively writing to.
#
# Usage:
#   ./scripts/backup.sh [data_dir] [backup_dir] [retention_days]
#
# Defaults match docker-compose.yml's volume mount and are safe to run via
# cron on the host, e.g.:
#   0 3 * * * cd /opt/porabot && ./scripts/backup.sh >> /var/log/porabot-backup.log 2>&1

set -euo pipefail

DATA_DIR="${1:-./data}"
BACKUP_DIR="${2:-./backups}"
RETENTION_DAYS="${3:-7}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 CLI not found — install it (apt install sqlite3) and retry." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

# Matches DATABASE_URL / SCHEDULER_DB_URL's filenames in docker-compose.yml.
backup_one() {
    local name="$1" src="$2"
    if [ ! -f "$src" ]; then
        echo "Skipping $src — not found." >&2
        return 0
    fi
    local dest="$BACKUP_DIR/${name}-${timestamp}.db"
    sqlite3 "$src" ".backup '$dest'"
    gzip "$dest"
    echo "Backed up $src -> ${dest}.gz"
}

backup_one porabot "$DATA_DIR/porabot.db"
backup_one jobs "$DATA_DIR/jobs.sqlite"

# Rotate: delete backups older than RETENTION_DAYS.
find "$BACKUP_DIR" -name '*.db.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "Backup complete. Retained the last ${RETENTION_DAYS} day(s) in $BACKUP_DIR."
