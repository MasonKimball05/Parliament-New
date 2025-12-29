# Parliament Security Guide

This guide covers security best practices, regular maintenance, and how to keep your Parliament installation secure.

## Table of Contents

1. [Security Features Implemented](#security-features-implemented)
2. [Regular Security Maintenance](#regular-security-maintenance)
3. [Security Audit](#security-audit)
4. [Updating Dependencies](#updating-dependencies)
5. [Incident Response](#incident-response)
6. [Security Checklist](#security-checklist)

---

## Security Features Implemented

### ✅ Protection Against Credential Stuffing/Password Guessing

**Features:**
- **Two-Factor Authentication (2FA)** for admins and officers
- **Rate limiting** on login attempts (5 attempts per 15 minutes)
- **Strong password requirements** (min 9 characters, complexity)
- **Password change enforcement** after admin resets

**How it works:**
- Admins/officers must set up Google Authenticator or similar app
- After entering password, they must enter a 6-digit code from their phone
- Even if password is stolen, attacker cannot log in without the phone

**User Impact:**
- Admins/officers: Required to set up 2FA on first login after implementation
- Regular members: Not required (can be enabled if desired)

### ✅ Protection Against SQL Injection & XSS

**Features:**
- **Django ORM** prevents SQL injection by using parameterized queries
- **Template auto-escaping** prevents XSS attacks
- **CSRF protection** on all POST forms
- **Content Security Policy** headers
- **Input validation** on file uploads

**How it works:**
- Django automatically escapes user input in templates
- All database queries use ORM, not raw SQL
- CSRF tokens prevent cross-site request forgery

**Monitoring:**
- Run `python3 security_audit.py` regularly to check for vulnerabilities
- Review security audit warnings and fix high-severity issues

### ✅ Additional Security Features

- **Field-level encryption** (usernames, emails, login IPs)
- **Session security** (HTTPOnly, Secure, SameSite cookies)
- **Login monitoring** (track suspicious login patterns)
- **Admin access logging** (audit trail of admin actions)
- **HTTPS enforcement** (production only)

---

## Regular Security Maintenance

### Monthly Tasks (15 minutes)

```bash
cd /var/www/Parliament-New
source venv/bin/activate

# 1. Check for outdated packages
pip list --outdated

# 2. Run security audit
python3 security_audit.py

# 3. Review login activity
python3 manage.py activity_logs

# 4. Check for failed login attempts
grep "Failed login" logs/django_actions.log | tail -20
```

### Quarterly Tasks (30 minutes)

```bash
# 1. Update Django and dependencies (see "Updating Dependencies" section)

# 2. Review user accounts
python3 manage.py shell
>>> from src.models import ParliamentUser
>>> inactive = ParliamentUser.objects.filter(member_status='Inactive')
>>> print(f"Inactive users: {inactive.count()}")
>>> # Consider removing old inactive accounts

# 3. Review admin users
>>> admins = ParliamentUser.objects.filter(is_admin=True)
>>> for admin in admins:
...     print(f"{admin.name} - Last login: {admin.last_login}")

# 4. Backup encryption key
# Store ENCRYPTION_KEY from .env in password manager
cat .env | grep ENCRYPTION_KEY
```

### Annual Tasks (1 hour)

```bash
# 1. Security review with officers
# - Review who has admin access
# - Review who has officer access
# - Update passwords for shared accounts

# 2. Review server security
ssh root@167.99.115.182
# Check SSH configuration
cat /etc/ssh/sshd_config | grep PasswordAuthentication  # Should be "no"

# Check fail2ban status
fail2ban-client status

# Review firewall rules
ufw status

# 3. Test disaster recovery
# - Verify backups are working
# - Test database restore
# - Verify encryption key backup
```

---

## Security Audit

### Running the Security Audit

```bash
python3 security_audit.py
```

This script checks for:
- SQL injection risks (raw SQL with string formatting)
- XSS vulnerabilities (unescaped template output)
- Missing CSRF protection
- Hardcoded secrets
- Insecure file uploads

### Understanding Results

**High Severity (RED)** - Fix immediately:
- SQL injection risks
- Missing CSRF tokens
- Hardcoded secrets in production code
- XSS with autoescape off

**Medium Severity (YELLOW)** - Review and fix if applicable:
- Use of `|safe` filter (ensure content is sanitized)
- Use of `mark_safe()` (ensure content is trusted)
- File uploads without validation

### Common Fixes

**SQL Injection:**
```python
# ❌ UNSAFE - String formatting
User.objects.raw(f"SELECT * FROM users WHERE id = {user_id}")

# ✅ SAFE - Parameterized query
User.objects.raw("SELECT * FROM users WHERE id = %s", [user_id])

# ✅ BETTER - Use ORM
User.objects.filter(id=user_id)
```

**XSS:**
```html
<!-- ❌ UNSAFE - Bypasses escaping -->
{{ user_comment|safe }}

<!-- ✅ SAFE - Auto-escaped -->
{{ user_comment }}

<!-- ✅ IF NEEDED - Sanitize first -->
{{ user_comment|escape|linebreaks }}
```

**CSRF:**
```html
<!-- ❌ UNSAFE - Missing CSRF token -->
<form method="post">
    <button>Submit</button>
</form>

<!-- ✅ SAFE - Includes CSRF token -->
<form method="post">
    {% csrf_token %}
    <button>Submit</button>
</form>
```

---

## Updating Dependencies

### Before Updating

```bash
# 1. Backup database
./shell/backup_db.sh

# 2. Test in local environment first
cd ~/Parliament  # Local development
source .venv/bin/activate
```

### Safe Update Process

```bash
# 1. Check what will be updated
pip list --outdated

# 2. Update one package at a time (not all at once!)
pip install --upgrade Django==5.1.8  # Example: specific version

# 3. Run tests
python3 manage.py check
python3 manage.py test

# 4. Test locally
python3 manage.py runserver

# 5. If everything works, update requirements.txt
pip freeze > requirements.txt

# 6. Commit changes
git add requirements.txt
git commit -m "Update Django to 5.1.8"
git push origin main
```

### Applying Updates to Production

```bash
# SSH to production server
ssh root@167.99.115.182
cd /var/www/Parliament-New

# Pull latest code
git pull origin main

# Update dependencies
source venv/bin/activate
pip install -r requirements.txt

# Run migrations (if any)
python3 manage.py migrate

# Collect static files
python3 manage.py collectstatic --noinput

# Restart service
sudo systemctl restart parliament-gunicorn

# Check status
sudo systemctl status parliament-gunicorn

# Monitor logs for errors
tail -f logs/django_actions.log
```

### Critical Security Updates

If a security advisory is released for Django:

1. **Assess severity** - Read the security announcement
2. **Update immediately** if it affects your version
3. **Test quickly** in local environment
4. **Deploy to production** within 24 hours
5. **Monitor logs** for any issues

Subscribe to security announcements:
- Django: https://www.djangoproject.com/weblog/
- Python: https://www.python.org/news/security/

---

## Incident Response

### Suspected Security Breach

1. **Immediate Actions** (within 1 hour):
   ```bash
   # Change all admin passwords
   # Disable compromised accounts
   python3 manage.py shell
   >>> from src.models import ParliamentUser
   >>> user = ParliamentUser.objects.get(username='compromised')
   >>> user.is_active = False
   >>> user.save()

   # Review recent login activity
   python3 manage.py activity_logs
   ```

2. **Investigation** (within 24 hours):
   ```bash
   # Check server access logs
   grep "Failed password" /var/log/auth.log

   # Review Django logs
   tail -100 logs/django_actions.log

   # Check for unauthorized changes
   git log --all --since="24 hours ago"
   ```

3. **Recovery** (within 48 hours):
   - Rotate encryption key (if compromised)
   - Reset all user passwords
   - Review and patch vulnerability
   - Update security measures

### Lost 2FA Device

If an admin/officer loses their phone:

```bash
# As superuser, disable 2FA for the user
python3 manage.py shell
>>> from django_otp.plugins.otp_totp.models import TOTPDevice
>>> from src.models import ParliamentUser
>>> user = ParliamentUser.objects.get(username='affected_user')
>>> TOTPDevice.objects.filter(user=user).delete()
>>> print("2FA disabled. User can set up again at next login.")
```

### Forgotten Encryption Key

⚠️ **CRITICAL**: If you lose the encryption key, encrypted data CANNOT be recovered.

**Prevention:**
- Store key in password manager (1Password, LastPass, etc.)
- Keep encrypted backup in secure location
- Document where key is stored

**If lost:**
1. Generate new encryption key
2. All new data will use new key
3. Old data will be unrecoverable (usernames, emails, IPs)
4. Consider fresh database migration if critical

---

## Security Checklist

### Production Deployment

- [ ] `DEBUG = False` in settings
- [ ] `SECRET_KEY` stored in environment variable
- [ ] `ENCRYPTION_KEY` stored in environment variable and backed up
- [ ] HTTPS enabled (SSL certificate)
- [ ] SSH password authentication disabled
- [ ] Firewall configured (ports 22, 80, 443 only)
- [ ] Fail2ban installed and configured
- [ ] Database backups automated
- [ ] Admin users have 2FA enabled
- [ ] Strong passwords enforced
- [ ] Regular security audits scheduled

### Code Security

- [ ] All forms include `{% csrf_token %}`
- [ ] No raw SQL queries with string formatting
- [ ] No `|safe` or `mark_safe` without sanitization
- [ ] File uploads validated (type, size)
- [ ] User input escaped in templates
- [ ] Secrets in environment variables, not code
- [ ] Dependencies up to date
- [ ] Security audit script passing

### Operational Security

- [ ] Monthly security maintenance completed
- [ ] Quarterly dependency updates completed
- [ ] Inactive users removed regularly
- [ ] Admin access reviewed regularly
- [ ] Login activity monitored
- [ ] Incident response plan documented
- [ ] Backups tested and verified

---

## Additional Resources

- [Django Security Documentation](https://docs.djangoproject.com/en/stable/topics/security/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Django Security Releases](https://www.djangoproject.com/weblog/)
- [Python Security](https://www.python.org/news/security/)

## Support

For security concerns or questions:
1. Run `python3 security_audit.py` first
2. Review this guide
3. Check Django security documentation
4. Consult with experienced Django developers if needed

**Never share secrets (encryption key, SECRET_KEY, passwords) via email or chat!**
