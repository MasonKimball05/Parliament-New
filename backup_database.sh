#!/usr/bin/env bash
# Parliament — nightly PostgreSQL backup (added 07-05-26).
#
# Reads DB credentials from the project .env, writes a compressed pg_dump
# custom-format archive to $BACKUP_DIR, and prunes archives older than
# $RETENTION_DAYS. Restore with:
#   pg_restore -d parliament_db -U parliament_user --clean --if-exists <file>
#
# Install (as root, one time):
#   cp backup_database.sh /usr/local/bin/parliament-backup && chmod +x /usr/local/bin/parliament-backup
#   cp parliament-backup.service parliament-backup.timer /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now parliament-backup.timer
#   systemctl list-timers parliament-backup*      # verify schedule
#   systemctl start parliament-backup.service     # test one run now
#
# NOTE: this protects against app/db mistakes, NOT server loss. Ship copies
# off the droplet (rclone to Backblaze B2 / S3, or scp in a cron) for real
# disaster recovery.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/Parliament-New}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/parliament}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# Pull DB_* out of the project .env without exporting everything
get_env() { grep -E "^$1=" "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-; }

DB_NAME="$(get_env DB_NAME)"
DB_USER="$(get_env DB_USER)"
DB_PASSWORD="$(get_env DB_PASSWORD)"
DB_HOST="$(get_env DB_HOST)"; DB_HOST="${DB_HOST:-localhost}"
DB_PORT="$(get_env DB_PORT)"; DB_PORT="${DB_PORT:-5432}"

if [[ -z "$DB_NAME" || -z "$DB_USER" ]]; then
    echo "ERROR: DB_NAME/DB_USER not found in $PROJECT_DIR/.env" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT="$BACKUP_DIR/${DB_NAME}_${STAMP}.dump"

export PGPASSWORD="$DB_PASSWORD"
pg_dump --format=custom --compress=9 \
    --host="$DB_HOST" --port="$DB_PORT" \
    --username="$DB_USER" "$DB_NAME" > "$OUT"
unset PGPASSWORD

chmod 600 "$OUT"

# Prune old backups
find "$BACKUP_DIR" -name "${DB_NAME}_*.dump" -mtime "+$RETENTION_DAYS" -delete

echo "Backup OK: $OUT ($(du -h "$OUT" | cut -f1)); retained: $(ls "$BACKUP_DIR" | wc -l) archives"
