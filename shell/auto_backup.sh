#!/bin/bash

DB_NAME="parliament_db"
DB_USER="masonkimball"
BACKUP_DIR="backups"
DATE=$(date +%F_%H-%M-%S)
FILENAME="$BACKUP_DIR/parliament_backup_$DATE.sql"

mkdir -p $BACKUP_DIR
pg_dump -U "$DB_USER" -d "$DB_NAME" -f "$FILENAME"

echo "✅ Backup saved to $FILENAME"
