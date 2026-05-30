#!/bin/bash
# restore_db.sh — Restore a Parliament database backup produced by backup_db
#
# Usage:
#   ./scripts/restore_db.sh /var/backups/parliament/parliament_2026-05-30_020000.dump
#
# To list available backups:
#   ls -lh /var/backups/parliament/
#
# What this does:
#   1. Stops gunicorn so no live traffic hits the DB during restore
#   2. Drops and recreates the database (--clean inside pg_restore handles this,
#      but we also do a full DROP/CREATE to guarantee a clean slate)
#   3. Runs pg_restore from the .dump file
#   4. Restarts gunicorn
#
# Run as root (or a user with sudo access and postgres permissions).

set -euo pipefail

DB_NAME="parliament_db"
DB_USER="parliament_user"
DB_HOST="localhost"
GUNICORN_SERVICE="parliament-gunicorn"
BACKUP_FILE="${1:-}"

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

if [[ -z "$BACKUP_FILE" ]]; then
    echo "ERROR: No backup file specified."
    echo ""
    echo "Usage: $0 /path/to/parliament_YYYY-MM-DD_HHMMSS.dump"
    echo ""
    echo "Available backups:"
    ls -lh /var/backups/parliament/*.dump 2>/dev/null || echo "  (none found in /var/backups/parliament/)"
    exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: File not found: $BACKUP_FILE"
    exit 1
fi

echo "=================================================="
echo " Parliament DB Restore"
echo "=================================================="
echo " Backup file : $BACKUP_FILE"
echo " Target DB   : $DB_NAME"
echo " DB user     : $DB_USER"
echo ""
read -r -p "This will WIPE the current database. Type 'yes' to continue: " confirm
if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# ---------------------------------------------------------------------------
# Stop gunicorn
# ---------------------------------------------------------------------------
echo ""
echo "[1/4] Stopping $GUNICORN_SERVICE …"
systemctl stop "$GUNICORN_SERVICE"

# ---------------------------------------------------------------------------
# Drop and recreate the database
# ---------------------------------------------------------------------------
echo "[2/4] Dropping and recreating $DB_NAME …"
sudo -u postgres psql -c "DROP DATABASE IF EXISTS $DB_NAME;"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
echo "[3/4] Restoring from $BACKUP_FILE …"
sudo -u postgres pg_restore \
    --dbname="$DB_NAME" \
    --host="$DB_HOST" \
    --username="$DB_USER" \
    --no-owner \
    --no-acl \
    --exit-on-error \
    "$BACKUP_FILE"

echo "      Restore complete."

# ---------------------------------------------------------------------------
# Restart gunicorn
# ---------------------------------------------------------------------------
echo "[4/4] Starting $GUNICORN_SERVICE …"
systemctl start "$GUNICORN_SERVICE"

echo ""
echo "=================================================="
echo " Done. Verify the site at https://am-parliament.org"
echo "=================================================="
