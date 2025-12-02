#!/bin/bash
DB_NAME="parliament_db"
DB_USER="masonkimball"

echo "⚠️ Resetting the database. All data will be lost."
dropdb -U "$DB_USER" "$DB_NAME"
createdb -U "$DB_USER" "$DB_NAME"
echo "✅ Database reset. Now applying migrations..."
python manage.py migrate
