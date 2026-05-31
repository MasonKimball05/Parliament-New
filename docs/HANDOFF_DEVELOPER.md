# Parliament — Developer Handoff Guide

**Last updated:** May 2026 (v2.26.0)
**Author:** Mason Kimball
**Live site:** https://am-parliament.org
**Repository:** https://github.com/MasonKimball05/Parliament-New

This document is written for whoever is taking over maintenance of Parliament. It covers everything you need to know that isn't obvious from reading the code — the server setup, deployment workflow, codebase quirks, and what to do when things break.

---

## Table of Contents

1. [What This App Is](#what-this-app-is)
2. [Tech Stack](#tech-stack)
3. [Production Server](#production-server)
4. [Services](#services)
5. [Deployment Workflow](#deployment-workflow)
6. [Database Backups](#database-backups)
7. [Codebase Architecture](#codebase-architecture)
8. [Key Gotchas](#key-gotchas)
9. [Feature Flags](#feature-flags)
10. [Management Commands](#management-commands)
11. [Third-Party Services](#third-party-services)
12. [When Things Break](#when-things-break)
13. [Accounts & Access](#accounts--access)

---

## What This App Is

Parliament is a chapter management platform built for the Alpha Mu chapter of Beta Theta Pi at Samford University. It handles:

- **Legislation & voting** — propose legislation, run votes (live or async), record results
- **Chapter minutes** — editor with motion recording, attendance sync, PDF generation
- **Committees** — membership, minutes, internal voting, document management
- **Slating & elections** — full officer election workflow including applications, voting, and transition system
- **Attendance tracking** — per-event tracking, excuse requests, officer dashboard, member self-view
- **Service hours** — submission, approval workflow, period requirements
- **Announcements** — with optional polls/surveys and document attachments
- **KAI reports** — member reporting system
- **Push notifications, security dashboard, activity logging**, and more

The app is intended to be maintainable by a future chapter member with basic Django knowledge. It is **not** a generic open-source product — it is tightly tailored to this chapter.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5.1, Python 3.13 |
| Database | PostgreSQL (production), SQLite (dev/test) |
| Frontend | Tailwind CSS (JIT), Alpine.js, vanilla JS |
| PDF generation | ReportLab |
| CSS build | `./build_css.sh` (wraps the `tailwindcss` CLI binary) |
| Web server | Nginx (reverse proxy) |
| App server | Gunicorn |
| Task queue | Celery + Redis (push notifications, scheduled tasks) |
| CDN/proxy | Cloudflare |
| Hosting | VPS (Ubuntu) |

---

## Production Server

**Domain:** am-parliament.org
**Server OS:** Ubuntu
**App directory:** `/var/www/Parliament-New`
**Python environment:** `/var/www/Parliament-New/venv`
**Static files:** `/var/www/Parliament-New/staticfiles`
**Media (uploads):** `/var/www/Parliament-New/media`
**Environment file:** `/var/www/Parliament-New/.env`
**Database backups:** `/var/backups/parliament/`
**Logs:** `/var/www/Parliament-New/logs/`

> **SSH access:** You'll need the server credentials from whoever currently has them. The SSH user and IP should be stored somewhere secure — ask outgoing officers or check the hosting provider dashboard.

---

## Services

There are three systemd services. The names are not obvious — don't use generic guesses.

| Service | What it does |
|---------|--------------|
| `parliament-gunicorn.service` | The Django app server. This is the one to restart after code changes. |
| `parliament-worker.service` | Celery worker. Handles async tasks (push notifications, etc.). |
| `parliament-beat.service` | Celery beat scheduler. Triggers periodic tasks (vote auto-close, log pruning, etc.). |

```bash
# Restart the app after a deployment
sudo systemctl restart parliament-gunicorn.service

# Check status / recent logs
sudo systemctl status parliament-gunicorn.service
sudo journalctl -u parliament-gunicorn.service -n 50

# Restart workers if push notifications or scheduled tasks stop working
sudo systemctl restart parliament-worker.service
sudo systemctl restart parliament-beat.service
```

> **Note:** The service file is `parliament-gunicorn.service`, not `parliament.service`. Using the wrong name will silently do nothing.

---

## Deployment Workflow

Do this every time you push code to production:

```bash
# 1. SSH into the server
ssh <user>@<server-ip>

# 2. Pull latest code
cd /var/www/Parliament-New
git pull origin main

# 3. If Python dependencies changed (requirements.txt was modified):
source venv/bin/activate
pip install -r requirements.txt

# 4. If models changed (new migrations):
python manage.py migrate

# 5. If static files or templates changed:
python manage.py collectstatic --noinput

# 6. Always restart the app server:
sudo systemctl restart parliament-gunicorn.service
```

### After any static file change — purge Cloudflare cache

Cloudflare aggressively caches static files. If you update CSS, JS, or images and collectstatic runs fine but the site looks wrong (missing styles, JS errors), Cloudflare is serving a cached version.

**Purge:** Cloudflare Dashboard → your domain → Caching → Purge Cache → Purge Everything.

This has caused "site has no styling" incidents where `curl` returned 200 but browsers got a cached 404. Always purge after changing static files.

### CSS rebuild (Tailwind)

If you change Tailwind classes in templates, you need to rebuild the CSS before deploying:

```bash
# On your local machine
./build_css.sh

# Then commit the updated staticfiles/css/output.css (or whatever the output is)
git add static/
git commit -m "Rebuild CSS"
git push
```

---

## Database Backups

Automated backups run via the `backup_db` management command, scheduled through Celery beat.

- **Location:** `/var/backups/parliament/`
- **Format:** PostgreSQL custom format (`.dump` files)
- **Retention:** 12 most recent backups kept, older ones pruned automatically
- **Schedule:** Weekly (Sunday 2am) September–May; monthly (1st, 2am) June–August

### Restoring from backup

A restore script is included at `scripts/restore_db.sh`. It handles stopping gunicorn, dropping and recreating the database, restoring from a dump file, and restarting the service.

```bash
# Restore from a specific backup file
sudo bash scripts/restore_db.sh /var/backups/parliament/<backup-file>.dump
```

Read the script before running it in an emergency. It requires `POSTGRES_DB`, `POSTGRES_USER`, and the dump file path.

### Manual backup

```bash
source venv/bin/activate
python manage.py backup_db
```

---

## Codebase Architecture

### Directory layout

```
Parliament/
├── src/
│   ├── models/          # Data models — split into 16 sub-modules
│   │   ├── __init__.py  # Re-exports everything; import from here
│   │   ├── users.py
│   │   ├── legislation.py
│   │   ├── committees.py
│   │   ├── documents.py
│   │   ├── announcements.py
│   │   ├── events.py    # Event, Attendance, AttendanceExcuse
│   │   ├── chat.py
│   │   ├── kai.py
│   │   ├── slating.py
│   │   ├── service.py
│   │   ├── security.py
│   │   ├── notifications.py
│   │   ├── activity.py
│   │   ├── guide.py
│   │   ├── songs.py
│   │   └── landing.py
│   ├── view/            # Views organized by area
│   │   ├── officer/     # Officer-only views (attendance, minutes, admin tools)
│   │   ├── committee/   # Committee views
│   │   ├── slating/     # Election/slating views
│   │   ├── chat/        # Chat views
│   │   └── ...          # Root-level views
│   ├── management/
│   │   └── commands/    # Management commands (see list below)
│   ├── migrations/      # Database migrations
│   ├── urls.py
│   ├── feature_flag_decorators.py
│   └── utils/
├── templates/           # Django templates (mirrors view structure)
├── static/              # Source static files
├── staticfiles/         # Collected static files (generated — don't edit)
├── Parliament/          # Django project settings
├── docs/                # This folder
└── scripts/             # Shell scripts (restore_db.sh, etc.)
```

### Import convention

All models are importable from `src.models` directly:

```python
from src.models import ParliamentUser, Event, Attendance, Legislation
```

The 16 sub-module files are the source of truth but `src/models/__init__.py` re-exports everything so no import sites need to know about the file split.

### Settings

- **Development:** `Parliament/settings.py`
- **Production:** Uses environment variables in `.env`. Run `python manage.py check_env` to validate all required variables are set.
- **CI:** `ci_settings.py`

### Authentication

Parliament uses a custom user model (`ParliamentUser`) — not Django's built-in `User`. Usernames are `user_id` values (fraternity-style member IDs). Passwords use Django's standard PBKDF2 hashing.

Member types: `Member`, `Chair`, `Officer`, `Advisor`, `Pledge`, `Alumni`. Many views gate functionality by member type.

---

## Key Gotchas

These are the things most likely to waste hours if you don't know them.

### 1. `Attendance.save()` silently overwrites the `present` field

The `Attendance` model has a legacy boolean field `present` (kept for backwards compatibility). The `save()` method **always sets it from `status`**:

```python
def save(self, *args, **kwargs):
    self.present = self.status == 'present'
    super().save(*args, **kwargs)
```

If you write `Attendance.objects.update_or_create(..., defaults={'present': True})`, the value is silently thrown away. **Always write `status='present'`** (or whatever status you intend), never the `present` field directly. The `present` field exists only for legacy queries — pretend it doesn't exist when writing new code.

### 2. CSP nonce required on all `<script>` tags

The app has a Content Security Policy that blocks inline scripts without a nonce. Every `<script>` tag in templates must have `nonce="{{ request.csp_nonce }}"`:

```html
<script nonce="{{ request.csp_nonce }}">
    // your JS here
</script>
```

Inline event handlers (`onclick=`, `onchange=`, etc.) are **not** covered by nonces and may be blocked on some paths. Use JavaScript event listeners added in a nonce'd script block instead.

### 3. Cloudflare caches 404s

If a static file didn't exist when Cloudflare first cached it, Cloudflare will serve that 404 forever — even after the file is present on origin. Purge cache after any deploy that adds new static files.

### 4. Minutes templates are shared

Both chapter minutes and committee minutes use the same template: `templates/officer/chapter_minutes_editor.html`. The view passes an `is_committee_minutes` context variable to branch behavior. Changes to this template affect both.

### 5. Service name is `parliament-gunicorn.service`

Not `parliament.service`. Wrong service name = silent failure.

### 6. Feature flags that are seeded but do nothing

Four feature flags are in the database and show up in the admin panel, but no view code checks them:

| Flag | Status |
|------|--------|
| `attendance_tracking` | No-op — all attendance views always active |
| `calendar_subscriptions` | No-op — feature partially built |
| `global_search` | No-op — search always active |
| `kai_reports` | No-op — KAI always active |

Toggling these in the admin panel has no effect. Either enforce them or remove them to avoid confusing future admins.

### 7. Login-as-user is under `/staff/`, not `/admin/`

The impersonation feature (`login_as_view`) lives at `/staff/login-as/<user_id>/`. It was moved from `/admin/` because Django admin catches all `admin/*` routes.

### 8. `user_id` is a CharField PK, not an integer

`ParliamentUser.user_id` is a string (e.g., `"A1234"`), not an integer. URL patterns that capture it use `<str:user_id>`, not `<int:user_id>`. This is a common mistake when writing new views.

---

## Feature Flags

The feature flag system is managed via the Admin v2 dashboard (`/admin-v2/`). Flags gate entire sections of the site.

| Flag | Controls |
|------|----------|
| `chats` | Committee and chapter chat |
| `announcements` | Announcements page and creation |
| `legislation` | Legislation and voting system |
| `slating` | Officer slating and elections |
| `service_hours` | Service hours submission and tracking |
| `house_map` | House map feature |
| `attendance_tracking` | **No-op** (see Gotchas) |
| `calendar_subscriptions` | **No-op** (see Gotchas) |
| `global_search` | **No-op** (see Gotchas) |
| `kai_reports` | **No-op** (see Gotchas) |

Page toggles (individual URL enable/disable) are also managed in Admin v2.

---

## Management Commands

Run these from `/var/www/Parliament-New` with the virtualenv active:

```bash
source venv/bin/activate
python manage.py <command>
```

| Command | What it does |
|---------|--------------|
| `backup_db` | Create a PostgreSQL dump in `/var/backups/parliament/` |
| `prune_activity_logs` | Delete old activity log entries (configurable retention) |
| `check_env` | Validate all required environment variables are set |
| `seed_feature_flags` | Seed the feature flag rows (idempotent — safe to re-run) |
| `seed_admin_v2` | Seed Admin v2 initial data |
| `reset_user_password <user_id>` | Reset a specific user's password |
| `reset_all_passwords` | Force all users to reset passwords on next login |
| `auto_close_votes` | Close votes past their deadline (also runs on schedule) |
| `archive_old_events` | Archive events older than 1 year |
| `cleanup_sessions` | Remove expired Django sessions |
| `process_scheduled_announcements` | Send any scheduled announcements that are due |
| `execute_scheduled_transitions` | Run any pending slating transitions |
| `import_from_exportable` | Import data from a `data_backup.json` export |

---

## Third-Party Services

| Service | Purpose | Where configured |
|---------|---------|-----------------|
| **Cloudflare** | CDN, DDoS protection, SSL | Cloudflare dashboard |
| **SMTP (email)** | Password reset, notifications | `.env` (EMAIL_HOST, etc.) |
| **Redis** | Celery broker, channel layer for real-time | `.env` (REDIS_URL) |
| **PostgreSQL** | Primary database | `.env` (DATABASE_URL or individual DB_* vars) |

---

## When Things Break

### Site is completely down (502 / no response)

```bash
sudo systemctl status parliament-gunicorn.service
sudo journalctl -u parliament-gunicorn.service -n 100
```

Most common causes: migration error, syntax error in Python code, out-of-memory kill, missing environment variable.

### Static files missing / site has no styling

1. Did you run `collectstatic`? → `python manage.py collectstatic --noinput`
2. Did you purge Cloudflare cache? → Dashboard → Caching → Purge Everything
3. Is Nginx configured to serve `/staticfiles/`? → Check `nginx.conf`

### Database connection error

Check `.env` for `DATABASE_URL` / `POSTGRES_*` vars. Check if PostgreSQL is running:

```bash
sudo systemctl status postgresql
```

### Push notifications not working

Celery worker may be down:

```bash
sudo systemctl status parliament-worker.service
sudo systemctl restart parliament-worker.service
```

### Scheduled tasks not running (auto-close votes, log pruning, etc.)

Celery beat may be down:

```bash
sudo systemctl status parliament-beat.service
sudo systemctl restart parliament-beat.service
```

### Error in the activity log / admin v2 shows nothing

Admin v2 is at `/admin-v2/`. Django admin (default) is at `/django-admin/`. Make sure you're using Admin v2 for Parliament-specific tools.

### "Page not found" for a page you know exists

Could be a disabled Page Toggle. Check Admin v2 → Page Toggles. Also check if the relevant feature flag is off.

---

## Accounts & Access

### Creating a superuser (first-time setup or emergency)

```bash
python manage.py createsuperuser
```

This creates a Django auth superuser. For a Parliament superuser (with full admin access in the app), set `member_type='Officer'` and `is_staff=True` on the `ParliamentUser` record.

### Admin panels

| URL | What it is |
|-----|-----------|
| `/admin-v2/` | Parliament's custom admin dashboard — use this for day-to-day admin |
| `/django-admin/` | Django's built-in admin — use only for direct DB manipulation if needed |

### Impersonating a user (debugging)

Officers can impersonate any member via `/staff/login-as/<user_id>/`. An amber banner appears at the top of every page while impersonating. All actions are logged to the activity log.

---

*For officer/admin feature usage (not technical), see [OFFICER_GUIDE.md](OFFICER_GUIDE.md).*
