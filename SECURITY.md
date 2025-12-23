# 🔒 Security Documentation

This document outlines the security measures implemented in Parliament and best practices for maintaining a secure deployment.

---

## Table of Contents

- [Security Improvements](#security-improvements)
- [Authentication & Authorization](#authentication--authorization)
- [Password Security](#password-security)
- [File Upload Security](#file-upload-security)
- [Database Security](#database-security)
- [Session & Cookie Security](#session--cookie-security)
- [HTTPS & Transport Security](#https--transport-security)
- [Security Headers](#security-headers)
- [Audit Logging](#audit-logging)
- [Security Checklist](#security-checklist)
- [Reporting Security Issues](#reporting-security-issues)

---

## Security Improvements

### Critical Changes Made

1. **✅ Fixed Login Authentication**
   - **Before**: Login used `username + user_id` (highly insecure!)
   - **After**: Proper authentication with `username + password`
   - Failed login attempts are logged for security audit

2. **✅ Restricted ALLOWED_HOSTS**
   - **Before**: `ALLOWED_HOSTS = ['*']` (accepts any host - vulnerable to Host header attacks)
   - **After**: Must be configured via environment variable
   - Default: `localhost,127.0.0.1`

3. **✅ Enforced SECRET_KEY Requirement**
   - **Before**: Fallback to `'fallback-secret'` (public knowledge)
   - **After**: Raises error if SECRET_KEY not set in production
   - Development has safe fallback with warning

4. **✅ Enhanced File Upload Validation**
   - Extension validation (`.pdf`, `.docx`, etc.)
   - **MIME type verification** (prevents extension spoofing)
   - File size limits (20 MB maximum)
   - Rejects files that don't match expected content type

5. **✅ Custom Password Complexity Requirements**
   - Minimum 9 characters
   - At least 1 uppercase letter
   - At least 1 lowercase letter
   - At least 1 number
   - At least 1 special symbol (`!@#$%^&*` etc.)

6. **✅ Admin Impersonation Logging**
   - All "login as" actions are logged to security log
   - Tracks who impersonated whom and when

---

## Authentication & Authorization

### Login System

**File**: `src/view/login_view.py`

```python
# Secure password-based authentication
user = authenticate(request, username=username, password=password)
```

**Security Features**:
- Uses Django's built-in `authenticate()` function
- Password hashing with PBKDF2-SHA256 (Django default)
- Failed login attempts logged to `logs/security.log`
- Checks for `is_active` status before allowing login
- Supports "next" URL parameter for redirect after login

### Admin Impersonation

**File**: `src/view/login_as_view.py`

**Security Measures**:
- Requires staff member privileges (`@staff_member_required`)
- Logs all impersonation events with admin and target user details
- Sets proper authentication backend

**Warning**: This feature should be disabled in production or restricted to superusers only.

---

## Password Security

### Password Validation Rules

**File**: `src/validators.py`

All passwords must meet these requirements:
- ✅ Minimum 9 characters
- ✅ At least 1 uppercase letter (A-Z)
- ✅ At least 1 lowercase letter (a-z)
- ✅ At least 1 number (0-9)
- ✅ At least 1 special symbol (!@#$%^&*()_+-=[]{} etc.)
- ✅ Cannot be too similar to username/name
- ✅ Cannot be a common password (checked against Django's list)

### Password Storage

- Hashed using **PBKDF2-SHA256** with 870,000 iterations
- Salted automatically by Django
- Never stored in plain text
- Never logged or transmitted without encryption

### Password Reset

**TODO**: Implement password reset functionality with:
- Email verification
- Secure token generation
- Time-limited reset links
- Rate limiting to prevent abuse

---

## File Upload Security

### Legislation Documents

**File**: `src/forms.py` - `LegislationForm`

**Validation Steps**:
1. **Extension Check**: Only `.pdf` and `.docx` allowed
2. **Size Check**: Maximum 20 MB
3. **MIME Type Verification**: Reads file header to verify actual file type
   - `application/pdf` for PDF files
   - `application/vnd.openxmlformats-officedocument.wordprocessingml.document` for DOCX

**Code Example**:
```python
def clean_document(self):
    file = self.cleaned_data.get('document')
    if file:
        # Check extension
        if not file.name.lower().endswith(('.pdf', '.docx')):
            raise ValidationError('Only PDF and DOCX files are allowed.')

        # Check size (20 MB max)
        if file.size > 20 * 1024 * 1024:
            raise ValidationError('File size must not exceed 20 MB.')

        # Verify MIME type (prevents .exe renamed to .pdf)
        mime = magic.from_buffer(file.read(2048), mime=True)
        if mime not in allowed_mimes:
            raise ValidationError('Invalid file type.')
```

### Committee Documents

**File**: `src/forms.py` - `CommitteeDocumentForm`

**Allowed Types**:
- PDF (`.pdf`)
- Word (`.doc`, `.docx`)
- Excel (`.xls`, `.xlsx`)
- PowerPoint (`.ppt`, `.pptx`)

**Security Measures**:
- Same validation as legislation documents
- MIME type verification for all file types
- Configurable via `settings.ALLOWED_DOCUMENT_TYPES`

### File Storage

**Settings**: `Parliament/settings_postgres.py`

```python
FILE_UPLOAD_PERMISSIONS = 0o644  # rw-r--r--
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755  # rwxr-xr-x
```

**Recommendations**:
- Store uploaded files outside web root
- Use content-disposition headers to force downloads
- Consider virus scanning for uploaded files
- Implement file retention policies

---

## Database Security

### Connection Security

**File**: `Parliament/settings_postgres.py`

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'OPTIONS': {
            'sslmode': 'prefer',  # Enable SSL for DB connections
        },
    }
}
```

### SQL Injection Protection

- **Django ORM** used throughout (parameterized queries)
- No raw SQL queries without proper escaping
- All user input validated and sanitized

### Database User Permissions

**Best Practices**:
- Use dedicated database user (not `postgres` superuser)
- Grant only necessary privileges (SELECT, INSERT, UPDATE, DELETE)
- Restrict network access to database (firewall rules)
- Use strong database passwords (different from Django SECRET_KEY)

### Database Backups

**Automated Backups**: See `shell/auto_backup.sh`

**Manual Backup**:
```bash
python manage.py dumpdata > backup_$(date +%Y%m%d).json
```

**Security**:
- Encrypt backup files
- Store backups separately from production server
- Test restore procedures regularly

---

## Session & Cookie Security

### Session Settings

**File**: `Parliament/settings_postgres.py`

```python
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_SECURE = True  # Only send over HTTPS
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
SESSION_COOKIE_AGE = 86400  # 24 hours
```

### CSRF Protection

```python
CSRF_COOKIE_SECURE = True  # Only send over HTTPS
CSRF_COOKIE_HTTPONLY = True  # Prevent JavaScript access
CSRF_COOKIE_SAMESITE = 'Lax'
```

**Implementation**:
- All forms include `{% csrf_token %}`
- POST requests require valid CSRF token
- Middleware validates tokens automatically

---

## HTTPS & Transport Security

### Production Settings

**File**: `Parliament/settings_postgres.py`

```python
# Only in production (DEBUG=False)
SECURE_SSL_REDIRECT = True  # Redirect HTTP to HTTPS
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HSTS (HTTP Strict Transport Security)
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
```

### SSL Certificate

**Recommended**: Use Let's Encrypt for free SSL certificates

```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

**Certificate Renewal**: Automatically handled by certbot

---

## Security Headers

### Implemented Headers

**File**: `Parliament/settings_postgres.py`

```python
SECURE_BROWSER_XSS_FILTER = True  # Enable XSS filter
SECURE_CONTENT_TYPE_NOSNIFF = True  # Prevent MIME-sniffing
X_FRAME_OPTIONS = 'DENY'  # Prevent clickjacking
SECURE_REFERRER_POLICY = 'same-origin'
```

### Additional Headers (Nginx)

**File**: `nginx.conf`

```nginx
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
```

---

## Audit Logging

### Log Files

**Location**: `logs/` directory

1. **`user_actions.log`**
   - User logins
   - Legislation uploads
   - Votes cast
   - Committee actions

2. **`security.log`**
   - Failed login attempts
   - Admin impersonation events
   - Permission violations
   - Suspicious activity

3. **`admin_actions.log`**
   - Admin panel actions
   - User management changes
   - System configuration changes

4. **`errors.log`**
   - Application errors
   - Exceptions
   - System failures

### Log Rotation

**File**: `/etc/logrotate.d/parliament`

```
/var/www/Parliament/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 parliament www-data
}
```

### Monitoring

**Recommendations**:
- Set up alerts for repeated failed logins
- Monitor for unusual admin impersonation
- Track large file uploads
- Alert on database errors

---

## Security Checklist

### Pre-Deployment

- [ ] Set `DEBUG = False` in production
- [ ] Generate strong `SECRET_KEY` (never commit to git)
- [ ] Configure `ALLOWED_HOSTS` with actual domain(s)
- [ ] Set strong database password
- [ ] Configure SSL/HTTPS certificates
- [ ] Enable database connection SSL (`DB_SSLMODE=require`)
- [ ] Review all environment variables in `.env`
- [ ] Change default admin passwords
- [ ] Disable SSH password authentication (use keys)

### Regular Maintenance

- [ ] Review security logs weekly
- [ ] Update dependencies monthly (`pip list --outdated`)
- [ ] Run `python manage.py check --deploy` before updates
- [ ] Test backups quarterly
- [ ] Rotate SECRET_KEY annually
- [ ] Review user permissions quarterly
- [ ] Audit admin impersonation logs monthly

### Django Security Check

```bash
python manage.py check --deploy
```

This command checks for common security issues.

---

## Reporting Security Issues

### How to Report

If you discover a security vulnerability, please:

1. **DO NOT** open a public GitHub issue
2. Email: mason.kimball@icloud.com
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if known)

### Response Timeline

- **24 hours**: Initial response
- **7 days**: Assessment and severity classification
- **30 days**: Patch development and testing
- **Public disclosure**: After patch is deployed

### Security Update Policy

- Critical vulnerabilities patched immediately
- High severity issues patched within 7 days
- Medium severity issues patched within 30 days
- Low severity issues addressed in next regular release

### Disclosure
- I am only a student developer, so my skills and experience are limited
- Please, bare with me and work with me on any issues that may arise I am still learning
---

## Additional Resources

### Security Best Practices

- [Django Security Documentation](https://docs.djangoproject.com/en/stable/topics/security/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Django Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Django_Security_Cheat_Sheet.html)

### Security Tools

- **Bandit**: Python security linter
  ```bash
  pip install bandit
  bandit -r src/
  ```

- **Safety**: Check dependencies for known vulnerabilities
  ```bash
  pip install safety
  safety check
  ```

- **Django Security Scanner**:
  ```bash
  python manage.py check --deploy
  ```

---

## Security Updates Log

### 2025-12-22

**Critical Security Update**

- ✅ Changed authentication from user_id to password-based
- ✅ Fixed ALLOWED_HOSTS vulnerability
- ✅ Enforced SECRET_KEY requirement in production
- ✅ Enhanced file upload validation with MIME type checking
- ✅ Implemented custom password complexity requirements
- ✅ Added security logging for admin impersonation
- ✅ Configured HTTPS/SSL security headers
- ✅ Added session and cookie security settings
- ✅ Enabled database connection SSL
- ✅ Documented all security measures

**Breaking Changes**:
- Users must set passwords (old user_id authentication removed)
- ALLOWED_HOSTS must be configured in `.env`
- SECRET_KEY must be set in production environment
- Passwords must meet new complexity requirements

---

**Last Updated**: 2025-12-22
**Security Contact**: mason.kimball@icloud.com
**Version**: 1.0.0
