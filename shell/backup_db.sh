#!/bin/bash

set -a
source .env
set +a

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")
OUTFILE="backups/parliament_backup_$TIMESTAMP.sql"

PGPASSWORD="$DB_PASSWORD" pg_dump -U "$DB_USER" -d "$DB_NAME" > "$OUTFILE"

echo "✅ Backup completed: $OUTFILE"
