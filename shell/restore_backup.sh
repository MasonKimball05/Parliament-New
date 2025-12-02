#!/bin/bash

# Load environment variables from .env
set -a
source .env
set +a

BACKUP_FILE=$1

if [ -z "$BACKUP_FILE" ]; then
  echo "❌ Error: Please provide the path to the backup file."
  echo "Usage: ./restore_backup.sh path/to/backup.sql"
  exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "❌ Error: Backup file '$BACKUP_FILE' not found."
  exit 1
fi

echo "⏳ Dropping existing database '$DB_NAME'..."
PGPASSWORD="$DB_PASSWORD" dropdb -U "$DB_USER" "$DB_NAME"

echo "✅ Creating new database '$DB_NAME'..."
PGPASSWORD="$DB_PASSWORD" createdb -U "$DB_USER" "$DB_NAME"

echo "📦 Restoring from backup file '$BACKUP_FILE'..."
PGPASSWORD="$DB_PASSWORD" psql -U "$DB_USER" -d "$DB_NAME" -f "$BACKUP_FILE"

echo "✅ Restore complete!"
