# Parliament - Chapter Management System
Copyright (C) 2026 Mason Kimball

A comprehensive Django-based management system for student organizations, designed to streamline legislation, voting, committee management, and chapter operations.

[![Django CI/CD](https://github.com/MasonKimball05/Parliament-New/workflows/Django%20CI%2FCD/badge.svg)](https://github.com/MasonKimball05/Parliament-New/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPLv3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.11.1-blue.svg)](changelogs/)

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Testing](#testing)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
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

### Service Hours
- **Member Dashboard** — submit hours, track progress toward period requirements
- **VPP Officer Dashboard** — approve/reject submissions, set period requirements, member overrides
- **Custom Form Builder** — VPP can add custom fields to the submission form
- **Approval Workflow** — approve, reject, or request changes with reviewer notes
- **Bulk Actions** — bulk approve/reject multiple submissions
- **CSV Export** — export submission data for reporting
- **Email Notifications** — VPP notified automatically when members submit hours

### Kai (Conduct) Reports
- **Report Filing** — members and officers can file conduct reports against active or inactive members
- **Inactive Member Support** — dropdown toggle to include alumni/inactive members as accused parties
- **Officer Management** — Kai committee manages and tracks all reports

### Events & Calendar
- **Event Management** — create and manage chapter events with dates, times, and locations
- **Calendar View** — visual calendar with all upcoming events
- **Calendar Subscriptions** — subscribe to auto-updating `.ics` feeds compatible with Google Calendar, Apple Calendar, and Outlook
- **iCal Export** — one-time export to calendar file
- **Automatic Archiving** — events older than 1 year automatically archived

### Attendance Tracking
- **Session Attendance** — mark members present/absent for meetings
- **Voting Eligibility** — only present members can vote (3-hour window)
- **Historical Records** — complete attendance history

### Announcements
- **Chapter-wide Announcements** — post announcements visible to all members
- **Email Notifications** — send email notifications to active members
- **Inactive Member Inclusion** — add individual inactive/alumni members to specific email sends
- **Email Warmup** — preview recipients and warm up email before sending

### Document Management
- **Chapter Documents** — upload constitutions, bylaws, and policies
- **Committee Documents** — committee-specific document repositories
- **Published/Unpublished** — control visibility of documents to the chapter
- **Document Types** — minutes, agendas, reports, policies, general documents

### Songbook
- **Chapter Songs** — full lyrics for 40 songs from the Beta Theta Pi Song Book (Revised 2005)
- **Proper Formatting** — verse/chorus structure with clean formatting

### Public Landing Page
- **Officer-Editable Content** — officers manage all public-facing text, photos, and sections via a WYSIWYG editor
- **Photo Library** — upload and embed photos inline with `[photo:ID:size:align]` shortcodes
- **Contact Form** — visitors submit messages routed to specific officers by topic/role
- **Contact Inbox** — all submissions saved to database; officer inbox with unread badge
- **Custom Form Links** — officers add externally-hosted recruitment forms and surveys
- **Social & External Links** — dynamic link manager (Instagram, Linktree, etc.)
- **Recruitment Banner** — dismissible banner with optional auto-expiry date
- **SEO & Open Graph** — meta description and OG image upload for link previews
- **Section Visibility** — show/hide individual sections without deleting content
- **In Development** — v2.12.0

### Admin v2 Dashboard
- **Card-Based Layout** — modern expandable card UI with Alpine.js
- **Dual Authentication** — password + secret key for enhanced security
- **Site-wide Statistics** — users, sessions, content counts, performance metrics
- **Feature Flag Management** — enable/disable features without code changes
- **Page Toggle Controls** — disable specific pages with custom messages
- **Comprehensive Audit Logging** — track all system actions and changes
- **Quarantine Management** — view and release quarantined accounts
- **Lockdown Controls** — emergency lockdown activation/deactivation
- **User ID Migration** — safely migrate a member's user ID in a single atomic transaction

### Security
- **Rate Limiting** — 5 failed login attempts triggers 15-minute lockout
- **Attack Detection Middleware** — detects and blocks SQL injection, XSS, path traversal, and command injection patterns
- **IP Blacklisting** — automatic and manual IP blocking
- **Honeypot Endpoints** — fake admin URLs (`/wp-admin/`, `/.env`, etc.) auto-ban scanners
- **Auto-Quarantine** — accounts triggering attack thresholds are automatically locked
- **Emergency Lockdown** — one-click mode blocks all logins except whitelisted IPs
- **Security Email Alerts** — critical events trigger immediate admin email notifications
- **Session Tracking** — active sessions visible on preferences page with device/IP info
- **Field-level Encryption** — sensitive fields (usernames, emails, IPs) encrypted at rest
- **Activity Logs** — full audit trail of all actions

---

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 13+
- pip and virtualenv

### Installation (5 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/MasonKimball05/Parliament-New.git
cd Parliament

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env with your settings

# 5. Run migrations
python manage.py migrate

# 6. Restore default committees and roles
python manage.py restore_committees_and_roles

# 7. Create superuser
python manage.py createsuperuser

# 8. Run the development server
python manage.py runserver
```

Visit `http://localhost:8000` to see your application.

---

## Installation

### System Requirements
```bash
# macOS
brew install postgresql python3

# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib python3 python3-pip python3-venv

# Verify installations
python3 --version  # Should be 3.11+
psql --version     # Should be 13+
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

```bash
# Generate a secure secret key
python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Key settings:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key — keep this secure |
| `DEBUG` | Set to `False` in production |
| `ALLOWED_HOSTS` | Comma-separated domain names |
| `DB_*` | Database connection settings |
| `TIME_ZONE` | Local timezone (default: `America/Chicago`) |
| `ENCRYPTION_KEY` | Key for field-level encryption — back this up |
| `SECURITY_ALERT_EMAIL` | Email address for critical security alerts |
| `DEFAULT_FROM_EMAIL` | From address for outgoing emails |

### Default Data

The system includes 11 pre-configured committees and 9 VP roles. Restore these after any database reset:

```bash
python manage.py restore_committees_and_roles
```

---

## Usage

### Creating Users

```python
from src.models import ParliamentUser

user = ParliamentUser.objects.create_user(
    user_id='12345',
    name='John Doe',
    username='jdoe',
    member_type='Member'
)
user.set_password('password')
user.save()
```

### Creating Legislation

1. Log in as Officer or Chair
2. Go to `/vote/`
3. Fill out the "Upload New Legislation" form
4. Select vote mode (Percentage / Piecewise / Plurality)
5. Set availability time and upload document
6. Submit

### Voting on Legislation

1. Members must be marked present (within 3-hour window)
2. Go to `/vote/`
3. Select vote choice and enter password to confirm

### Managing Committees

1. Navigate to `/committees/`
2. Chairs can upload documents, create committee votes, manage members, view minutes, and push legislation to chapter

---

## Testing

```bash
# Run all tests
python manage.py test

# Run with coverage
pip install coverage
coverage run --source='.' manage.py test
coverage report
```

See [TESTING.md](TESTING.md) for detailed documentation.

---

## Deployment

### Docker

```bash
docker-compose up -d
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

### Production

See [DEPLOYMENT.md](DEPLOYMENT.md) for full instructions including Gunicorn, Nginx, SSL, and database backups.

---

## Management Commands

```bash
# Restore committees and VP roles
python manage.py restore_committees_and_roles

# Archive events older than 1 year
python manage.py archive_old_events

# Dry run
python manage.py archive_old_events --dry-run

# Database backup
python manage.py dumpdata > backup.json

# Clear expired attendance records
python manage.py clear_expired_attendance

# Django security check
python manage.py check --deploy
```

---

## Project Structure

```
Parliament/
├── Parliament/                 # Project settings
│   ├── base_settings.py
│   ├── settings_postgres.py
│   └── wsgi.py
├── src/
│   ├── middleware/             # Security, session tracking, lockdown
│   ├── management/commands/    # Custom management commands
│   ├── templatetags/           # Custom template filters
│   ├── view/                   # View modules
│   │   ├── officer/            # Officer portal views
│   │   ├── committee/          # Committee views
│   │   ├── slating/            # Election/slating views
│   │   └── admin_v2.py         # Admin v2 views
│   ├── models.py               # Database models
│   ├── forms.py                # Django forms
│   └── urls.py                 # URL routing
├── templates/                  # HTML templates
│   ├── admin_v2/               # Admin v2 dashboard
│   ├── officer/                # Officer portal templates
│   ├── committee/              # Committee templates
│   └── slating/                # Election templates
├── static/                     # Static files (CSS, JS, images)
├── exportable_media/           # Media served directly by nginx
├── changelogs/                 # Per-version detailed changelogs
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
├── requirements.txt
└── manage.py
```

---

## Security

Parliament includes a multi-layered security system. See [SECURITY.md](SECURITY.md) and [SECURITY_GUIDE.md](SECURITY_GUIDE.md) for full details.

**Key protections:**
- CSRF, XSS, and SQL injection protection
- Rate limiting on login (5 attempts / 15 min)
- Attack detection middleware with auto IP blocking
- Honeypot endpoints that auto-ban scanners
- Auto-quarantine for attack-pattern accounts
- Emergency lockdown mode
- Field-level encryption for sensitive data
- Security email alerts for critical events
- Password hashing with PBKDF2-SHA256

Report vulnerabilities to: mason.kimball@icloud.com

---

## Changelog

See the [changelogs/](changelogs/) directory for detailed per-version release notes.

| Version | Status | Summary |
|---------|--------|---------|
| [v2.12.0](changelogs/v2.12.0-landing-page-overhaul.md) | In Development | Landing page officer editor, contact form routing, photo library, SEO |
| [v2.11.1](changelogs/v2.11.1.md) | Deployed | Timezone fixes, announcement inactive members, co-authors, legislation bugs |
| [v2.11.0](changelogs/v2.11.0.md) | Deployed | Attack mitigation (quarantine, honeypots, lockdown), admin v2 redesign |
| [v2.10.0](changelogs/v2.10.0.md) | Deployed | Songbook lyrics, pledge initiation fixes |
| [v2.9.0](changelogs/v2.9.0.md) | Deployed | Login rate limiting, SQL injection/XSS middleware, IP blacklisting |
| [v2.8.x](changelogs/v2.8.6.md) | Deployed | Service hours system, directory export, chat, calendar subscriptions |
| [v2.7.x](changelogs/v2.7.0-slating-system.md) | Deployed | Officer slating/election system |
| [v2.6.x](changelogs/v2.6.4.md) | Deployed | Admin v2 dashboard, feature flags, page toggles |

---

## Tech Stack

- **Backend**: Django 4.2+, Python 3.11+
- **Database**: PostgreSQL 15
- **Frontend**: HTML, Tailwind CSS, Alpine.js, JavaScript
- **Rich Text**: Quill.js (officer editors)
- **Authentication**: Django Auth with field-level encryption
- **File Storage**: Django FileField (local / S3-compatible)
- **Cache**: Redis (session throttling, rate limiting, performance metrics)
- **CI/CD**: GitHub Actions
- **Deployment**: Docker, Gunicorn, Nginx

---

## License
This project is licensed under the GNU Affero General Public License v3.0.
See the [LICENSE](LICENSE) file for details.

Source code is available at:
https://github.com/MasonKimball05/Parliament-New
---

## Authors

- **Mason Kimball** — [MasonKimball05](https://github.com/MasonKimball05)
---

## Support

- **Issues**: [GitHub Issues](https://github.com/MasonKimball05/Parliament-New/issues)
- **Email**: mason.kimball@icloud.com