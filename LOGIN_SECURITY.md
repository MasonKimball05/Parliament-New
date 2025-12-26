# Login & Authentication Security Features

## Overview
Comprehensive security measures to protect user accounts and prevent unauthorized access attempts.

---

## Security Features

### 1. **Login Rate Limiting**
Prevents brute force attacks by limiting login attempts from the same IP or targeting the same username.

**IP-based Limits:**
- Maximum 10 login attempts per IP within 15 minutes
- After exceeding limit, IP is locked out for 30 minutes
- Applies to all login attempts regardless of username

**Username-based Limits:**
- Maximum 5 login attempts per username within 15 minutes
- Prevents targeted attacks against specific accounts
- Lockout persists even from different IPs

**Implementation:** `src.middleware.LoginRateLimitMiddleware`

### 2. **Account Lockout System**
Automatically locks accounts after repeated failed login attempts.

**Triggers:**
- 5 failed password attempts for the same username
- Lockout duration: 30 minutes
- Clear message with password reset option

**User Experience:**
```
Account Temporarily Locked

This account has been temporarily locked due to multiple failed login attempts.

Please try again in 30 minutes, or use the "Forgot Password" link to reset your password.

← Back to Login
Reset Password
```

### 3. **Comprehensive Audit Logging**
All authentication events are logged for security monitoring.

**Logged Events:**

**Successful Logins:**
```
LOGIN SUCCESS: User 'mkimball' (ID: 73) from IP 192.168.1.100
```

**Failed Login Attempts:**
```
Failed login attempt for username "mkimball" from IP 192.168.1.100.
IP attempts: 3/10, Username attempts: 3/5
```

**Rate Limit Violations:**
```
Login rate limit exceeded for IP 192.168.1.100. Attempts: 10
Login blocked: Username mkimball is locked out. Attempt from IP 192.168.1.100
```

**Disabled Account Access:**
```
LOGIN FAILED: Attempt to access disabled account 'mkimball' from IP 192.168.1.100
```

**Missing Credentials:**
```
Login attempt with missing credentials from IP 192.168.1.100
```

**Logs Location:** `logs/django_actions.log`

### 4. **IP Address Tracking**
All login attempts are tracked with the source IP address.

**Features:**
- Proxy-aware (reads X-Forwarded-For header)
- Works with Cloudflare and other CDNs
- Logs actual client IP, not proxy IP

**IP Detection Logic:**
```python
# Checks X-Forwarded-For first (for proxies/CDNs)
# Falls back to REMOTE_ADDR
# Used across all security middleware
```

### 5. **Admin Panel Access Monitoring**
Special monitoring and logging for admin panel access.

**Monitored Activities:**
- Admin login attempts (unauthenticated)
- Unauthorized access attempts (non-admin users)
- All admin actions (POST, PUT, PATCH, DELETE)

**Example Logs:**

**Successful Admin Action:**
```
ADMIN ACTION: User 'admin' (POST /admin/src/parliamentuser/73/change/) from IP 192.168.1.100
```

**Unauthorized Access:**
```
ADMIN ACCESS DENIED: Non-admin user 'mkimball' attempted to access /admin/ from IP 192.168.1.100
```

**Implementation:** `src.middleware.AdminAccessMonitoringMiddleware`

### 6. **Automatic Counter Reset**
Failed attempt counters are automatically cleared upon successful login.

**Cleared on Success:**
- IP-based attempt counter
- Username-based attempt counter
- IP lockout flag
- Username lockout flag

---

## Configuration

### Rate Limit Settings
Located in `src/middleware.py` - `LoginRateLimitMiddleware`:

```python
self.max_attempts_per_ip = 10          # Max attempts per IP per window
self.max_attempts_per_username = 5      # Max attempts per username per window
self.window_minutes = 15                # Time window in minutes
self.lockout_minutes = 30               # Lockout duration after exceeding limits
```

### Middleware Stack
Located in `Parliament/settings_postgres.py`:

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'src.middleware.PasswordResetRateLimitMiddleware',  # Password reset protection
    'src.middleware.LoginRateLimitMiddleware',           # Login brute force protection
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'src.middleware.AdminAccessMonitoringMiddleware',    # Admin access logging
    'src.middleware.ForcePasswordChangeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

### Cache Configuration
Rate limiting uses Django's cache system:

```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'parliament-cache',
    }
}
```

---

## Monitoring & Alerts

### View Login Activity
Check logs for authentication events:

```bash
# View all login attempts
tail -f logs/django_actions.log | grep "LOGIN"

# View failed login attempts
grep "Failed login attempt" logs/django_actions.log

# View lockout events
grep "locked out" logs/django_actions.log

# View successful logins from specific IP
grep "LOGIN SUCCESS.*192.168.1.100" logs/django_actions.log

# View admin panel access
grep "ADMIN" logs/django_actions.log
```

### Admin Dashboard
View logs through the admin panel:
- Navigate to: `/admin/view-logs/`
- Search for "LOGIN" or "ADMIN" entries

### Set Up Alerts (Recommended)
Monitor for suspicious patterns:

```bash
# Alert on multiple lockouts (potential distributed attack)
grep -c "locked out" logs/django_actions.log

# Alert on admin access from new IPs
# Track known admin IPs and alert on others

# Alert on disabled account access attempts
grep "disabled account" logs/django_actions.log
```

---

## Testing Security Features

### Test Rate Limiting
Try logging in with wrong password multiple times:

```bash
# Test IP-based rate limiting (11 attempts)
for i in {1..11}; do
  curl -X POST http://localhost:8080/login/ \
    -d "username=testuser&password=wrongpassword" \
    --cookie-jar cookies.txt \
    --cookie cookies.txt
  echo "Attempt $i"
  sleep 1
done
```

### Test Username Lockout
Try same username from different IPs (6 attempts):

After the 5th attempt, the account should be locked.

### Test Successful Login Clears Counters
1. Make 3 failed login attempts
2. Login successfully with correct password
3. Verify counters are cleared (check logs or cache)

---

## Troubleshooting

### User Locked Out Legitimately

**Option 1: Wait for expiration**
- Account lockouts expire after 30 minutes
- IP lockouts expire after 30 minutes

**Option 2: Manually clear lockout (admin only)**
```bash
# SSH into server
cd /var/www/Parliament-New
source venv/bin/activate

# Clear specific lockout
python3 manage.py shell
>>> from django.core.cache import cache
>>> cache.delete('login_lockout_user_mkimball')
>>> cache.delete('login_attempts_user_mkimball')
>>> cache.delete('login_lockout_ip_192.168.1.100')
```

**Option 3: Password reset**
- User can use "Forgot Password" link
- Resets password and clears attempt counters

### Check Current Lockout Status

```bash
python3 manage.py shell

from django.core.cache import cache

# Check IP attempts
cache.get('login_attempts_ip_192.168.1.100')

# Check if IP is locked out
cache.get('login_lockout_ip_192.168.1.100')

# Check username attempts
cache.get('login_attempts_user_mkimball')

# Check if username is locked out
cache.get('login_lockout_user_mkimball')
```

### Rate Limiting Not Working

Check that:
1. All middleware is enabled in settings
2. Cache backend is configured correctly
3. Django server has been restarted
4. No errors in logs

### View All Active Lockouts

```bash
python3 manage.py shell

from django.core.cache import cache

# This requires access to cache internals
# Depends on cache backend used
```

---

## Production Recommendations

### 1. Use Redis for Distributed Systems
If running multiple web servers, use Redis for shared cache:

```python
# In settings_postgres.py
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}
```

### 2. Implement Automated Alerting

**Example using log monitoring:**
```bash
# /etc/cron.hourly/security-alerts.sh

LOCKOUT_COUNT=$(grep -c "locked out" /var/www/Parliament-New/logs/django_actions.log)

if [ $LOCKOUT_COUNT -gt 10 ]; then
    echo "High number of account lockouts detected: $LOCKOUT_COUNT" | \
        mail -s "Security Alert" admin@example.com
fi
```

### 3. Regular Security Reviews

**Weekly:**
- Review login failure patterns
- Check for unusual IP addresses
- Monitor admin access logs

**Monthly:**
- Analyze lockout trends
- Review rate limit thresholds
- Update security policies

### 4. Adjust Rate Limits Based on Usage

**For high-traffic sites:**
- Increase `max_attempts_per_ip` to 20-30
- Keep `max_attempts_per_username` low (5-10)

**For high-security environments:**
- Decrease `max_attempts_per_username` to 3
- Increase lockout duration to 60 minutes
- Implement stricter admin access controls

### 5. IP Whitelisting for Admin

**For known admin IPs:**
```python
# Add to AdminAccessMonitoringMiddleware
ADMIN_ALLOWED_IPS = ['192.168.1.100', '10.0.0.50']

if ip_address not in ADMIN_ALLOWED_IPS:
    logger.warning(f"Admin access from non-whitelisted IP: {ip_address}")
    # Optionally block access
```

---

## Security Best Practices

### Strong Password Requirements
Already implemented in `src/validators.py`:
- Minimum 9 characters
- 1 uppercase, 1 lowercase, 1 number, 1 symbol
- Not similar to username
- Not a common password

### Session Security
Configured in `Parliament/settings_postgres.py`:
```python
SESSION_COOKIE_SECURE = True      # HTTPS only
SESSION_COOKIE_HTTPONLY = True    # No JavaScript access
SESSION_COOKIE_AGE = 2592000      # 30 days
```

### CSRF Protection
All forms include CSRF tokens:
```html
<form method="POST">
    {% csrf_token %}
    ...
</form>
```

### HTTPS Enforcement
In production:
```python
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
```

---

## Summary

The login security system includes:

✅ IP-based rate limiting (10 attempts / 15 min)
✅ Username-based rate limiting (5 attempts / 15 min)
✅ Automatic account lockouts (30 min)
✅ Comprehensive audit logging
✅ IP address tracking (proxy-aware)
✅ Admin panel access monitoring
✅ Automatic counter reset on success
✅ User-friendly lockout messages
✅ Password reset integration
✅ Production-ready caching

These protections make brute force attacks impractical while maintaining excellent user experience for legitimate users.

---

**Last Updated:** 2025-12-25
**Related Documentation:** PASSWORD_RESET_SECURITY.md, SECURITY.md
