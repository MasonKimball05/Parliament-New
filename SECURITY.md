# Security Documentation

This document outlines the security measures implemented in Parliament and best practices for maintaining a secure deployment.

**Last Updated:** April 12, 2026 | **Version:** 2.11.1 | **Contact:** mason.kimball@icloud.com

---

## Table of Contents

- [Security Features](#security-features)
- [Authentication & Authorization](#authentication--authorization)
- [Attack Detection & Mitigation](#attack-detection--mitigation)
- [Password Security](#password-security)
- [Encryption](#encryption)
- [File Upload Security](#file-upload-security)
- [Database Security](#database-security)
- [Session & Cookie Security](#session--cookie-security)
- [HTTPS & Transport Security](#https--transport-security)
- [Security Headers](#security-headers)
- [Audit Logging](#audit-logging)
- [Security Checklist](#security-checklist)
- [Reporting Security Issues](#reporting-security-issues)
- [Security Updates Log](#security-updates-log)

---

## Security Features

### Authentication
- Password-based authentication via Django's `authenticate()` with PBKDF2-SHA256 hashing
- Login rate limiting — 5 failed attempts triggers 15-minute lockout per IP
- Account quarantine — accounts flagged by attack detection cannot log in until admin-released
- Session tracking — active sessions visible to users on the preferences page with device/IP info
- Password complexity enforcement — min 9 chars, uppercase, lowercase, number, special character

### Attack Detection & Blocking
- `InputSanitizationMiddleware` scans all incoming requests for SQL injection, XSS, path traversal, and command injection patterns
- After 10 attacks from a single IP in 1 hour: IP automatically blocked for 1 hour
- After 20 attacks: associated user account is auto-quarantined
- Manual IP blacklisting available via Admin v2 dashboard
- Security email alerts sent to `SECURITY_ALERT_EMAIL` on critical events

### Honeypot Endpoints
Fake admin URLs that real users would never access. Any hit triggers an immediate 24-hour IP ban and security alert:
- `/wp-admin/`
- `/phpmyadmin/`
- `/.env`
- `/admin/backup/`
- `/api/v1/users/export/`

### Emergency Lockdown
One-click lockdown mode (`SystemLockdown` singleton) blocks all login attempts except IPs on the whitelist. A user-facing maintenance page is shown. Only admins can activate/deactivate. All activations are logged with admin identity and timestamp.

### Field-Level Encryption
Sensitive fields (usernames, emails, login IPs) are encrypted at rest using a configurable `ENCRYPTION_KEY`. If this key is lost, encrypted data cannot be recovered — back it up.

---

## Authentication & Authorization

### Login System

**File:** `src/view/login_view.py`

- Uses Django's built-in `authenticate()` with PBKDF2-SHA256
- Checks `is_active` and `is_quarantined` flags before authenticating
- Failed attempts logged to `logs/security.log`
- Rate limiting enforced per IP (5 attempts / 15-minute window)
- Supports `?next=` redirect after login

### Role-Based Access Control

| Role | Access |
|------|--------|
| Member | Voting, attendance, personal profile, service hours |
| Chair | Committee management, committee votes, document upload |
| Officer | Officer portal, all officer views, landing page editor |
| Advisor | Same as Officer |
| Admin | Admin v2 dashboard, user management, security tools |

### Admin v2 Dual Authentication

Admin v2 requires both a valid officer/admin account **and** a secret key configured in the environment. This prevents access even if an officer account is compromised.

### Admin Impersonation

**File:** `src/view/login_as_view.py`

All impersonation events are logged with admin identity, target user, and timestamp. Requires staff member privileges.

---

## Attack Detection & Mitigation

### InputSanitizationMiddleware

**File:** `src/middleware/security.py`

Scans query params, POST body, and headers on every request. Detected patterns:
- SQL injection (UNION, SELECT, DROP, INSERT, etc.)
- XSS (`<script>`, `javascript:`, event handlers)
- Path traversal (`../`, `..\\`)
- Command injection (`;`, `|`, backtick, etc.)

**Behavior:**
- Attacks logged with IP, path, pattern matched
- 10+ attacks from one IP in 1 hour → IP blocked for 1 hour
- 20+ attacks → associated user account auto-quarantined

**Skip paths** (endpoints that receive free-text legitimately):
- `/contact/submit/` — public contact form
- `/officers/edit-landing-page/` — rich text HTML content

### Auto-Quarantine

**File:** `src/middleware/security.py`, `src/models.py`

When an IP exceeds the attack threshold, `ParliamentUser.is_quarantined` is set to `True`. The login view rejects quarantined accounts. Admin can release via Admin v2 → Quarantine Management or Django admin.

### Honeypot System

**File:** `src/view/honeypot.py`

Access to any honeypot URL:
1. Logs the access with IP, user agent, and timestamp to `HoneypotAccess`
2. Adds the IP to `IPBlacklist` with a 24-hour ban
3. Sends a security alert email

### Emergency Lockdown

**File:** `src/middleware/lockdown.py`

- `LockdownMiddleware` runs on every request
- If `SystemLockdown.is_active`, non-whitelisted IPs are redirected to `templates/lockdown.html`
- Activation/deactivation recorded with admin identity and timestamp
- Whitelist is a comma-separated list of IPs in `SystemLockdown.whitelist_ips`

### Security Email Alerts

**File:** `src/security_notifications.py`

Critical events that trigger email to `SECURITY_ALERT_EMAIL`:
- IP blocked after repeated attacks
- Account auto-quarantined
- Honeypot endpoint accessed
- Emergency lockdown activated/deactivated

---

## Password Security

### Validation Rules

**File:** `src/validators.py`

All passwords must meet:
- Minimum 9 characters
- At least 1 uppercase letter (A-Z)
- At least 1 lowercase letter (a-z)
- At least 1 number (0-9)
- At least 1 special symbol (`!@#$%^&*` etc.)
- Cannot be too similar to username/name
- Cannot be a common password (Django's built-in list)

### Storage

- Hashed using PBKDF2-SHA256 with 870,000 iterations
- Salted automatically by Django
- Never stored in plain text or logged

### Password Reset

Password reset is implemented via email-based token links. Tokens are time-limited and single-use.

---

## Encryption

### Field-Level Encryption

Sensitive model fields are encrypted at rest:
- `ParliamentUser.username`
- `ParliamentUser.email`
- Login IP addresses in session/login history records

**Key management:**
- `ENCRYPTION_KEY` must be set in the environment
- Store the key in a password manager and maintain an encrypted backup
- If the key is lost, encrypted data is permanently unrecoverable
- Key rotation requires re-encrypting all existing records

---

## File Upload Security

### Legislation Documents

**File:** `src/forms.py` — `LegislationForm`

1. Extension check — only `.pdf` and `.docx` allowed
2. Size check — maximum 20 MB
3. MIME type verification — reads file header to confirm actual type (prevents `.exe` renamed to `.pdf`)

### Committee Documents

Additional allowed types: `.doc`, `.xls`, `.xlsx`, `.ppt`, `.pptx`

Same extension + size + MIME validation as legislation documents.

### Landing Page Photos

Uploaded via officer editor. Stored in Django's media directory with standard file permissions.

---

## Database Security

### Connection

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'OPTIONS': {'sslmode': 'prefer'},
    }
}
```

### SQL Injection Protection

- Django ORM used throughout — parameterized queries by default
- `InputSanitizationMiddleware` blocks injection patterns at the HTTP layer as a secondary defense

### Best Practices

- Use a dedicated database user (not `postgres` superuser)
- Grant only SELECT, INSERT, UPDATE, DELETE
- Restrict network access via firewall
- Automate backups — see `shell/auto_backup.sh`

---

## Session & Cookie Security

```python
SESSION_COOKIE_SECURE = True      # HTTPS only
SESSION_COOKIE_HTTPONLY = True    # No JavaScript access
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
SESSION_COOKIE_AGE = 86400        # 24 hours
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
```

Session records are tracked in `UserSession` via `SessionTrackingMiddleware` (throttled to once per 5 minutes per session to reduce DB load). Users can view their active sessions on the preferences page.

---

## HTTPS & Transport Security

```python
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000       # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
```

---

## Security Headers

**Django settings:**
```python
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
```

**Nginx (`nginx.conf`):**
```nginx
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
```

---

## Audit Logging

### Log Files (`logs/` directory)

| File | Contents |
|------|----------|
| `user_actions.log` | Logins, votes, uploads, committee actions |
| `security.log` | Failed logins, blocked IPs, quarantines, impersonation, honeypot hits, lockdown events |
| `admin_actions.log` | Admin panel changes, user management, system configuration |
| `errors.log` | Application errors and exceptions |

### Database Audit Models

| Model | Purpose |
|-------|---------|
| `ActivityLog` | All user actions with actor, target, and timestamp |
| `LoginHistory` | Per-user login history with IP and device |
| `IPBlacklist` | Blocked IPs with reason and expiry |
| `HoneypotAccess` | Honeypot endpoint hits |
| `QuarantinedAccount` | Quarantine records with reason, admin, and release info |
| `SecurityNotificationLog` | Record of all security alert emails sent |

---

## Security Checklist

### Pre-Deployment

- [ ] `DEBUG = False`
- [ ] Strong `SECRET_KEY` in environment (never committed)
- [ ] `ALLOWED_HOSTS` set to actual domain(s)
- [ ] `ENCRYPTION_KEY` set and backed up securely
- [ ] `SECURITY_ALERT_EMAIL` configured
- [ ] Strong database password
- [ ] SSL/HTTPS certificates configured
- [ ] `DB_SSLMODE=require` in production
- [ ] Default admin passwords changed
- [ ] SSH password authentication disabled (use keys)

### Regular Maintenance

- [ ] Review `logs/security.log` weekly
- [ ] Review blocked IPs and quarantined accounts in Admin v2
- [ ] Update dependencies monthly (`pip list --outdated`)
- [ ] Run `python manage.py check --deploy` before updates
- [ ] Test database backups quarterly
- [ ] Rotate `SECRET_KEY` annually
- [ ] Review user and admin accounts quarterly
- [ ] Audit impersonation logs monthly

### Django Security Check

```bash
python manage.py check --deploy
```

---

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Email: mason.kimball@icloud.com
3. Include: description, steps to reproduce, potential impact, and suggested fix

**Response timeline:**
- 24 hours: Initial response
- 7 days: Severity classification
- 30 days: Patch development and testing
- Public disclosure after patch is deployed

*Note: This is a student-developed project. Please be patient and constructive — I'm still learning.*

---

## Security Updates Log

### April 7, 2026 (v2.11.0)
- Auto-quarantine system for accounts triggering attack thresholds
- Honeypot/poison pill endpoints (`/wp-admin/`, `/.env`, etc.)
- Emergency lockdown mode with whitelist
- Session tracking middleware — active sessions now visible on preferences page
- Security email notifications for critical events (`SECURITY_ALERT_EMAIL`)
- Fixed IP blacklist enforcement in security middleware
- Fixed honeypot `cache.set` argument order

### April 1, 2026 (v2.9.0)
- Login rate limiting (5 attempts / 15 minutes)
- `InputSanitizationMiddleware` — SQL injection, XSS, path traversal, command injection detection
- Automatic IP blocking after 10 attacks in 1 hour
- IP blacklisting management in Admin v2
- Security headers added to all responses

### March 2026 (v2.8.x)
- Field-level encryption for usernames, emails, and login IPs
- Session management — users can view and manage active sessions

### December 22, 2025 (v2.0)
- Changed authentication from `user_id` to password-based
- Fixed `ALLOWED_HOSTS = ['*']` vulnerability
- Enforced `SECRET_KEY` requirement in production
- Enhanced file upload validation with MIME type checking
- Custom password complexity requirements
- Security logging for admin impersonation
- HTTPS/SSL security headers configured
- Session and cookie security settings
- Database connection SSL enabled
