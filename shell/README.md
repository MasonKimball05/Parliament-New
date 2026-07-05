# shell/ — One-off Management Scripts

These scripts wrap common `manage.py` and database operations. Run them from the repo root (`Parliament-New/`) unless noted otherwise.

> **Convention:** When a script has been run on production, note it below under "Prod run" so future maintainers know the state.

---

## Scripts

### Database & Backup

| Script | Purpose | Prod run |
|--------|---------|----------|
| `backup_db.sh` | PostgreSQL backup using `.env` credentials. Writes to `backups/parliament_backup_TIMESTAMP.sql`. Preferred for production. | Ongoing |
| `auto_backup.sh` | Simpler PostgreSQL backup (hardcodes `masonkimball` as DB user, no `.env`). Use `backup_db.sh` instead on prod. | Dev only |
| `restore_backup.sh` | Restore from a `.sql` backup file. Reads credentials from `.env`. | As needed |
| `db_dump.sh` | Dumps all model data to stdout for quick inspection. Not a real backup — use `backup_db.sh` for backups. | Dev only |
| `reset_db.sh` | **Destructive.** Drops and recreates the database. Dev/staging only. | No |

### Migrations & Deployment

| Script | Purpose | Prod run |
|--------|---------|----------|
| `apply_migrations.sh` | Runs `python manage.py migrate`. Shortcut for production deploys. | Ongoing |
| `make_migrations.sh` | Runs `python manage.py makemigrations`. Dev only (migrations are not tracked in git). | Dev only |
| `collect_static.sh` | Runs `python manage.py collectstatic --noinput`. Run after deploying new static assets. Remember to purge Cloudflare cache afterward. | Ongoing |
| `seed_feature_flags.sh` | Seeds all feature flags and page toggles to the database. Run once on a fresh environment or to add new flags. | Yes (initial setup) |
| `migrate_users.sh` | **Legacy one-time script.** Migrated user data from SQLite → PostgreSQL during initial setup. Do not re-run. Uses `shell/users.json` as intermediary — that file is gitignored. | Yes (completed) |

### User Management

| Script | Purpose | Prod run |
|--------|---------|----------|
| `add_members.sh` | Interactive prompt to add a new member to the database. | As needed |
| `apply_admin_to_user.sh` | Lists all users and promotes a chosen user to admin + officer. | As needed |
| `reset_password.sh` | Resets a single user's password to default format and forces a password change on next login. Usage: `./reset_password.sh <user_id> [custom_password]` | As needed |
| `reset_all_passwords.sh` | Resets all user passwords except excluded IDs. Usage: `./reset_all_passwords.sh 73,72,67 [optional_temp_password]` | As needed |
| `fix_usernames.sh` | Normalizes all usernames to standard format, excluding specified IDs. Usage: `./fix_usernames.sh 73,72,67` | As needed |
| `view_members.sh` | Prints all member names, usernames, and IDs. Quick lookup utility. | N/A (read-only) |
| `mark_present.sh` | Marks one user as present by user_id. Usage: `./mark_present.sh USERID` | As needed |

### Onboarding

| Script | Purpose | Prod run |
|--------|---------|----------|
| `grandfather_onboarding.sh` | **One-time.** Marks all established users (non-default password + email set) as `onboarding_complete=True` so they skip the onboarding wizard. Run after deploying v3.6.2+ and applying migration 0207. **Idempotent — safe to re-run.** | Yes (after v3.6.2 deploy) — verify |

### Monitoring & Diagnostics

| Script | Purpose | Prod run |
|--------|---------|----------|
| `log_summary.sh` | Greps `logs/django_actions.log` by keyword and shows last 30 matches. Usage: `./log_summary.sh KEYWORD` | N/A (read-only) |
| `monitor_email_logs.sh` | Tails email setting logs in real-time on production. | N/A (read-only) |
| `server_maintenance.sh` | Clears expired sessions, old logs, and prevents memory/DB bloat. Recommended as a daily cron at 3 AM: `0 3 * * * /var/www/Parliament-New/shell/server_maintenance.sh` | Yes (cron) |
| `test_vote_integrity.sh` | Runs `check_passed_legislation.py` to verify vote consistency. | As needed |

---

## Notes

- All scripts assume they're run from the repo root (`Parliament-New/`) unless they use `SCRIPT_DIR`.
- Scripts that call `manage.py` inherit whichever Django settings are active. On prod this is `Parliament.settings`.
- `users.json` in this directory is a **legacy migration artifact** from `migrate_users.sh`. It is gitignored. Do not commit it — it contains user PII.
