#!/bin/bash
set -e

SCRIPT_DIR=$(dirname "$0")
USER_FIXTURE="$SCRIPT_DIR/users.json"

echo "==> [1] Dumping ParliamentUser data from SQLite..."
python ../manage.py dumpdata src.ParliamentUser --settings=Parliament.settings_sqlite --output="$USER_FIXTURE"

echo "==> [2] Applying migrations in PostgreSQL..."
python ../manage.py migrate --settings=Parliament.settings_postgres

echo "==> [3] Loading ParliamentUser data into PostgreSQL..."
python ../manage.py loaddata "$USER_FIXTURE" --settings=Parliament.settings_postgres

echo "==> ✅ Migration complete."
