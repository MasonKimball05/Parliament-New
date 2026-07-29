# Parliament - Chapter Management System
Copyright (C) 2026 Mason Kimball

A comprehensive Django-based management platform for the Alpha Mu chapter of Beta Theta Pi at Samford University. Handles legislation, voting, elections, committees, attendance, service hours, events, real-time chat, and chapter administration.

[![Django CI/CD](https://github.com/MasonKimball05/Parliament-New/workflows/Django%20CI%2FCD/badge.svg)](https://github.com/MasonKimball05/Parliament-New/actions)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.2+](https://img.shields.io/badge/django-5.2+-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-3.17.0-blue.svg)](changelogs/)

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Management Commands](#management-commands)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [Security](#security)
- [Changelog](#changelog)

---

## Features

### Legislation & Voting
- **Multiple Vote Modes** — Percentage (yes/no with configurable thresholds), Piecewise (exact count), and Plurality (multi-choice)
- **Anonymous Voting** — optional secret ballot mode
- **Co-Authorship** — add co-authors to legislation who share edit access
- **Authorship Transfer** — transfer primary ownership to another member
- **Real-time Vote Tallies** — live counts visible to legislation authors
- **Vote History** — comprehensive archive of all past legislation

### Officer Slating & Elections
- **Modular Slating System** — fully configurable officer election workflow without code changes
- **Dynamic Application Forms** — committee chairs build custom forms with any field types
- **GPA & Eligibility Checks** — automatically enforces constitutional requirements per position
- **Secret Ballot** — configurable voting with 60% threshold and up to 3 slate attempts
- **Interview Notes** — confidential notes with destruction capability post-election
- **Transition System** — structured handoff workflow between outgoing and incoming officers

### Committee Management
- **Committee Structure** — pre-configured default committees (Brotherhood, Finance, Education, etc.)
- **Committee Legislation** — separate voting system for committee-level decisions
- **Document Management** — upload and share minutes, agendas, reports, and policies
- **Exec Committee Minutes** — dedicated minutes editor with PDF generation and document sync
- **Member Roles** — chairs, members, voting members, and advisors
- **Push to Chapter** — promote approved committee legislation to chapter-wide vote

### Recruitment Module
- **Recruitment Dashboard** — committee-level dashboard with tabbed Events and Candidates views
- **Recruitment Events** — create and manage rush events with public/committee-only visibility, RSVP or attendance tracking, and private officer notes
- **Candidate Tracking** — structured candidate pipeline (Prospect → Bid Accepted/Declined) with name, contact info, status, assigned member, notes, and last-contacted date
- **Permission Tiers** — chair-level vs. general member access; private notes and candidate data gated by role
- **Calendar Integration** — recruitment events appear on the chapter calendar

### Service Hours
- **Member Dashboard** — submit hours, track progress toward period requirements
- **VPP Officer Dashboard** — approve/reject submissions, set period requirements, member overrides
- **Service Events** — VPP creates service events on the chapter calendar; present members are automatically awarded hours when attendance is finalized. Supports per-member hours overrides and scheduled email reminders via Celery
- **Custom Form Builder** — VPP can add custom fields to the submission form
- **Approval Workflow** — approve, reject, or request changes with reviewer notes
- **Bulk Actions** — bulk approve/reject multiple submissions
- **CSV Export** — export submission data for reporting
- **Email Notifications** — VPP notified automatically when members submit hours

### Events & Calendar
- **Event Management** — create and manage chapter events with dates, times, and locations
- **Calendar View** — visual monthly calendar with event modals
- **Sign-Up Events** — events can require members to explicitly sign up. Supports an optional hard cap (`max_signups`), open/close toggle, and an optional waitlist — members queue when the event is full and are auto-promoted when a slot opens
- **Sign-Up Roster** — officers view confirmed + waitlisted members from the sign-up list dashboard; CSV export available
- **Calendar Subscriptions** — subscribe to auto-updating `.ics` feeds compatible with Google Calendar, Apple Calendar, and Outlook
- **iCal Export** — one-time export to calendar file
- **Automatic Archiving** — events older than 1 year automatically archived

### Real-Time Chat
- **WebSocket Channels** — real-time messaging via Django Channels + Redis (no page refresh)
- **Channel Management** — officers create and manage channels; member access controlled per channel
- **Unread Badges** — per-channel unread counts in nav, resolved with a single annotated SQL query
- **New Messages Divider** — visual marker for the first unread message when opening a channel
- **Message Deletion** — soft-delete with audit trail
- **Committee Channels** — committees have dedicated channels separate from chapter-wide channels

### Push Notifications
- **In-App Bell** — real-time notification bell fed by the WebSocket connection
- **Notification Types** — event reminders, security alerts (lockouts, suspicious logins), API token expiry, service event reminders, and more
- **Email Fallback** — users without a registered device still receive email for critical alerts
- **User Preferences** — members opt in/out of specific notification categories

### Announcements
- **Chapter-wide Announcements** — post announcements visible to all members or specific member types
- **Email Notifications** — send email to active members with optional inactive/alumni inclusion
- **Attached Documents** — upload PDFs or images alongside announcement text
- **Email Warmup** — preview recipients before sending

### Document Management
- **Chapter Documents** — upload constitutions, bylaws, and policies
- **Committee Documents** — committee-specific document repositories
- **Published/Unpublished** — control visibility of documents to the chapter
- **Document Types** — minutes, agendas, reports, policies, general documents

### Songbook
- **Chapter Songs** — full lyrics for 40 songs from the Beta Theta Pi Song Book (Revised 2005) with verse/chorus formatting

### Public Landing Page
- **Officer-Editable Content** — all public-facing text, photos, and sections managed via a WYSIWYG editor
- **Photo Library** — upload and embed photos with `[photo:ID:size:align]` shortcodes
- **Contact Form** — visitor messages routed to specific officers by topic/role
- **Custom Form Links** — officers add externally-hosted recruitment forms and surveys
- **Social & External Links** — dynamic link manager (Instagram, Linktree, etc.)
- **SEO & Open Graph** — meta description and OG image upload for link previews

### Admin v2 Dashboard
- **Card-Based Layout** — modern expandable card UI
- **Dual Authentication** — password + secret key for enhanced security
- **Site-wide Statistics** — users, sessions, content counts, performance metrics
- **Feature Flag Management** — enable/disable features without code changes
- **Page Toggle Controls** — disable specific pages with custom messages
- **Audit Log Viewer** — paginated, filterable log of all system actions; CSV export
- **Quarantine Management** — view, release, and auto-expire quarantined accounts
- **Lockdown Controls** — emergency lockdown activation/deactivation
- **User ID Migration** — safely migrate a member's user ID in a single atomic transaction

### Security
- **Rate Limiting** — 5 failed login attempts triggers a 15-minute per-account lockout, enforced in Redis
- **Attack Detection Middleware** — detects SQL injection, XSS, path traversal, and command injection patterns; auto-blocks IPs after threshold
- **Honeypot Endpoints** — fake admin URLs (`/wp-admin/`, `/.env`, etc.) auto-ban scanners
- **Auto-Quarantine** — accounts triggering attack thresholds are automatically locked, with optional time-based expiry
- **Emergency Lockdown** — one-click mode blocks all logins except whitelisted IPs
- **Passkeys (WebAuthn)** — passwordless FIDO2 login registered to the member's device
- **Two-Factor Authentication** — TOTP authenticator app support with backup codes
- **Breached Password Detection** — new passwords checked against known breach databases
- **Field-Level Encryption** — sensitive fields (usernames, emails, IPs) encrypted at rest with Fernet
- **In-App Security Notifications** — lockouts and suspicious logins surface in the notification bell
- **Security Email Alerts** — critical events trigger immediate admin email notifications
- **Session Tracking** — active sessions visible on the preferences page with device/IP info
- **Activity Logs** — full audit trail of all actions, viewable from Admin v2

### Kai (Conduct) Reports
- **Report Filing** — members and officers can file conduct reports
- **Inactive Member Support** — dropdown toggle to include alumni/inactive members
- **Officer Management** — Kai committee manages and tracks all reports

---

## Quick Start

### Prerequisites
- Python 3.13+
- PostgreSQL 15+
- Redis 7+
- pip and virtualenv

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/MasonKimball05/Parliament-New.git
cd Parliament-New

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env with your database, Redis, email, and encryption settings

# 5. Run migrations
python manage.py migrate

# 6. Restore default committees and roles
python manage.py restore_committees_and_roles

# 7. Register Celery beat schedules
python manage.py setup_celery_schedules

# 8. Create superuser
python manage.py createsuperuser

# 9. Run the development server
python manage.py runserver
```

For WebSocket support in development, use Daphne instead:
```bash
daphne -p 8000 Parliament.asgi:application
```

Visit `http://localhost:8000` to see your application.

---

## Installation

### System Requirements
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib python3 python3-pip python3-venv redis-server

# macOS
brew install postgresql python3 redis
```

### Database Setup
```bash
psql postgres
```

```sql
CREATE DATABASE parliament_db;
CREATE USER parliament_user WITH PASSWORD 'your_password';
ALTER ROLE parliament_user SET client_encoding TO 'utf8';
ALTER ROLE parliament_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE parliament_user SET timezone TO 'America/Chicago';
GRANT ALL PRIVILEGES ON DATABASE parliament_db TO parliament_user;
\q
```

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key — never commit this |
| `DEBUG` | Set to `False` in production |
| `ALLOWED_HOSTS` | Comma-separated domain names |
| `DB_*` | Database connection settings |
| `REDIS_URL` | Redis connection URL (default: `redis://localhost:6379/0`) |
| `TIME_ZONE` | Local timezone (default: `America/Chicago`) |
| `ENCRYPTION_KEY` | Fernet key for field-level encryption — **back this up** |
| `SECURITY_ALERT_EMAIL` | Email address for critical security alerts |
| `DEFAULT_FROM_EMAIL` | From address for outgoing emails |
| `EMAIL_HOST` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | SMTP settings |

### Default Data

Restore the 11 pre-configured committees and 9 VP roles after any database reset:

```bash
python manage.py restore_committees_and_roles
```

---

## Management Commands

Run from the repo root with the virtualenv active:

| Command | Description |
|---------|-------------|
| `restore_committees_and_roles` | Seed default committees and officer roles |
| `setup_celery_schedules` | Register all periodic Celery tasks with django-celery-beat |
| `archive_old_events` | Archive events older than 1 year (`--dry-run` available) |
| `clear_expired_attendance` | Remove expired attendance records |
| `check --deploy` | Django deployment security checklist |

---

## Project Structure

```
Parliament-New/
├── Parliament/                 # Django project settings (tracked in this repo)
│   ├── settings.py             # Unified settings — env-driven via .env
│   ├── asgi.py                 # ASGI entry point (WebSocket support via Daphne)
│   └── wsgi.py
├── src/
│   ├── middleware/             # Security, session tracking, lockdown, input sanitization
│   ├── management/commands/    # Custom management commands
│   ├── templatetags/           # Custom template filters and tags
│   ├── tasks/                  # Celery tasks (notifications, reminders, housekeeping)
│   ├── utils/                  # Shared helpers (cache utils, officer checks, etc.)
│   ├── models/                 # Database models (split by domain)
│   │   ├── events.py           # Event, Attendance, EventSignup
│   │   ├── chat.py             # ChatChannel, ChatMessage, ChatReadReceipt
│   │   ├── committees.py       # Committee, CommitteeRole
│   │   ├── recruitment.py      # RecruitmentEvent, RecruitmentCandidate
│   │   ├── service.py          # ServiceEvent, ServiceHoursSubmission, ServicePeriod
│   │   └── ...
│   ├── view/                   # View modules (split by feature area)
│   │   ├── officer/            # Event management, attendance, manage events
│   │   ├── committee/          # Committee home, recruitment dashboard
│   │   ├── chat/               # Channel chat, WebSocket consumers
│   │   └── slating/            # Officer election workflow
│   ├── consumers.py            # Django Channels WebSocket consumers
│   ├── forms.py                # Django forms
│   ├── context_processors.py   # Per-request template context (Redis-cached)
│   ├── decorators.py           # Role-based access decorators
│   └── urls.py                 # URL routing
├── templates/                  # HTML templates (Tailwind + Alpine.js)
│   ├── admin_v2/
│   ├── calendar/
│   ├── chat/
│   ├── committee/
│   ├── officer/
│   ├── service_hours/
│   └── slating/
├── static/                     # Static files (CSS, JS, images)
├── changelogs/                 # Per-version detailed release notes
├── docs/                       # Developer and officer guides
│   ├── HANDOFF_DEVELOPER.md    # Onboarding guide for future maintainers
│   └── OFFICER_GUIDE.md
├── shell/                      # Utility scripts and local-only data exports
├── requirements.txt
├── manage.py
└── build_css.sh                # Tailwind CSS build script
```

> **Note:** Django settings live in the `Parliament/` git submodule. Changes there do not appear in `git status` on the outer repo.

> **Note:** Migrations are intentionally gitignored. They are authored and applied directly on production — a `git pull` never delivers migration files.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5.1, Python 3.13 |
| Database | PostgreSQL 15 (production) |
| Real-time | Django Channels 4.2, Daphne 4.1 (ASGI / WebSocket) |
| Task queue | Celery 5.4, django-celery-beat, Redis |
| Cache | Redis + django-redis |
| Frontend | Tailwind CSS (JIT via CLI), Alpine.js, vanilla JS |
| Rich text | Quill.js |
| PDF generation | ReportLab |
| Authentication | Django Auth, django-otp (TOTP / 2FA), webauthn 2.7 (passkeys) |
| REST API | Django REST Framework |
| CDN / proxy | Cloudflare |
| Web server | Nginx (reverse proxy) + Gunicorn |
| CI/CD | GitHub Actions |

---

## Security

Parliament has a multi-layered security system. See [SECURITY.md](SECURITY.md) and [SECURITY_GUIDE.md](SECURITY_GUIDE.md) for full details.

**Key protections:**
- CSRF, XSS, and SQL injection protection (Django defaults + custom middleware)
- Per-account rate limiting — 5 attempts / 15 min, enforced in Redis across all processes
- Passkey (WebAuthn / FIDO2) support for passwordless login
- TOTP two-factor authentication with backup codes
- Breached password detection on registration and password change
- Attack detection middleware with auto IP blocking and auto-quarantine
- Honeypot endpoints that auto-ban scanners
- Emergency lockdown mode
- Fernet field-level encryption for sensitive data at rest
- Security events surfaced via both email and in-app notification bell
- Full activity audit log in Admin v2

Report vulnerabilities to: mason.kimball@icloud.com

---

## Changelog

See the [changelogs/](changelogs/) directory for detailed per-version release notes.

| Version | Type | Summary |
|---------|------|---------|
| [v3.10.0](changelogs/v3.10.0.md) | Feature / Fix | Event waitlist, signup CSV export, service attendance bulk refactor, several bug fixes |
| [v3.9.1](changelogs/v3.9.1.md) | Performance | Context processor caching, chat unread N+1 fix, home view query reduction, composite indexes |
| [v3.9.0](changelogs/v3.9.0.md) | Feature / Security | Audit log viewer, auto-expiring quarantines, onboarding widget, activity logging sweep |
| [v3.8.0](changelogs/v3.8.0.md) | Feature | In-app security notifications (lockouts, suspicious logins), API token expiry alerts |
| [v3.7.1](changelogs/v3.7.1.md) | Bug Fix | Token scope fix, login pipeline deduplication, lockout observability |
| [v3.7.0](changelogs/v3.7.0.md) | Security | Per-account lockout, breached password detection, shorter session lifetime, CI security gates |
| [v3.6.0](changelogs/v3.6.0.md) | Feature | Sign-up events, recruitment module, service events, candidate tracking |
| [v3.0.0](changelogs/v3.0.0.md) | Major | WebSocket chat, passkeys, Constitution & Bylaws builder, security hardening |

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

Source code: https://github.com/MasonKimball05/Parliament-New

---

## Authors

**Mason Kimball** — [MasonKimball05](https://github.com/MasonKimball05)

---

## Support

- **Issues**: [GitHub Issues](https://github.com/MasonKimball05/Parliament-New/issues)
- **Email**: mason.kimball@icloud.com
- **Developer handoff guide**: [docs/HANDOFF_DEVELOPER.md](docs/HANDOFF_DEVELOPER.md)
