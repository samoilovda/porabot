#!/usr/bin/env bash
# Verify the most recent backup(s) produced by scripts/backup.sh actually
# restore to a valid database, right after backup.sh runs in the deploy
# pipeline (step 6, 2026-09-26 audit remediation) — so a corrupt or
# incomplete backup fails the deploy loudly instead of being discovered
# only when someone actually needs to restore from it.
#
# Usage:
#   ./scripts/verify_backup.sh [backup_dir]
#
# For the latest porabot-*.db.gz and jobs-*.db.gz in backup_dir:
#   - gunzip to a temp file
#   - PRAGMA integrity_check must return exactly "ok"
#   - a SELECT count(*) against each of that DB's expected tables must
#     succeed (proves the schema actually restored, not just that the
#     file opens) — the row count itself is only printed, not enforced,
#     since a fresh install's backup is legitimately empty.
#
# Exit code is 1 if any check fails, 0 if every backup found verifies
# clean. A backup that doesn't exist yet (e.g. jobs.sqlite absent on a
# fresh install) is skipped, not a failure — mirrors backup.sh's own
# "Skipping ... not found" behavior.

set -euo pipefail

BACKUP_DIR="${1:-./backups}"

if command -v sqlite3 >/dev/null 2>&1; then
    SQLITE_TOOL=cli
elif command -v python3 >/dev/null 2>&1 && python3 -c "import sqlite3" >/dev/null 2>&1; then
    SQLITE_TOOL=python
else
    echo "Neither the sqlite3 CLI nor python3 (with its sqlite3 module) is available — install one (apt install sqlite3) and retry." >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAILED=0

_integrity_check() {
    local db="$1"
    if [ "$SQLITE_TOOL" = cli ]; then
        sqlite3 "$db" "PRAGMA integrity_check;"
    else
        python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" "$db"
    fi
}

_count_table() {
    local db="$1" table="$2"
    if [ "$SQLITE_TOOL" = cli ]; then
        sqlite3 "$db" "SELECT count(*) FROM $table;"
    else
        python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('SELECT count(*) FROM ' + sys.argv[2]).fetchone()[0])" "$db" "$table"
    fi
}

verify_one() {
    local label="$1"; shift
    local latest
    latest="$(ls -t "$BACKUP_DIR"/"${label}"-*.db.gz 2>/dev/null | head -n1 || true)"
    if [ -z "$latest" ]; then
        echo "Skipping $label — no backup found in $BACKUP_DIR." >&2
        return 0
    fi

    local restored="$TMP_DIR/${label}.db"
    gunzip -c "$latest" > "$restored"

    local integrity
    if ! integrity="$(_integrity_check "$restored" 2>&1)"; then
        echo "FAIL: $latest — could not open for integrity_check: $integrity" >&2
        FAILED=1
        return 0
    fi
    if [ "$integrity" != "ok" ]; then
        echo "FAIL: $latest failed integrity_check: $integrity" >&2
        FAILED=1
        return 0
    fi

    local table count
    for table in "$@"; do
        if ! count="$(_count_table "$restored" "$table" 2>&1)"; then
            echo "FAIL: $latest — SELECT count(*) FROM $table failed: $count" >&2
            FAILED=1
            continue
        fi
        echo "OK: $latest — $table: $count row(s)"
    done
}

verify_one porabot users reminders habit_events payments
verify_one jobs apscheduler_jobs

if [ "$FAILED" -ne 0 ]; then
    echo "Backup verification FAILED." >&2
    exit 1
fi

echo "Backup verification OK."
