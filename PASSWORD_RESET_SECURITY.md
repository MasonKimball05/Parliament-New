# Password Reset Security Features

## Overview
The password reset system has been enhanced with multiple layers of security to prevent brute force attacks and unauthorized access attempts.

## Security Features

### 1. **Rate Limiting**
Prevents attackers from making unlimited password reset requests.

**IP-based limits:**
- Maximum 5 attempts per IP address within 15 minutes
- After 5 attempts, IP is locked out for 60 minutes
- Applies to all requests from the same IP regardless of email

**Email-based limits:**
- Maximum 3 attempts per email address within 15 minutes
- Prevents targeted attacks against specific accounts
- Limits persist even if requests come from different IPs

### 2. **Cryptographically Secure Tokens**
Django generates password reset tokens using:
- User's password hash (token invalidates when password changes)
- User's last login timestamp
- Django's SECRET_KEY (must be kept secret and strong)
- Timestamp for expiration checking
- HMAC signing for tamper protection

**Token format:** `http://domain.com/password-reset-confirm/<uidb64>/<token>/`
- `uidb64`: Base64-encoded user ID
- `token`: Cryptographically signed token (40+ characters)

### 3. **Shortened Expiration Window**
- Reset links expire after **30 minutes** (reduced from 1 hour)
- Minimizes the window for token compromise
- Users can request a new link if expired

### 4. **Comprehensive Logging**
All password reset activities are logged:

**Successful requests:**
```
Password reset requested for email user@example.com from IP 192.168.1.100
IP attempts: 1/5, Email attempts: 1/3
```

**Rate limit violations:**
```
Password reset rate limit exceeded for IP 192.168.1.100. Attempts: 5
Password reset blocked: IP 192.168.1.100 is locked out
```

**Logs location:** `logs/django_actions.log`

### 5. **No Email Enumeration**
The system doesn't reveal whether an email exists:
- Always shows "If this email exists, we've sent a reset link" message
- Same response time regardless of email validity
- Prevents attackers from discovering valid user emails

### 6. **Single-Use Tokens**
- Each token can only be used once
- After password change, token becomes permanently invalid
- Prevents replay attacks

### 7. **IP-based Detection**
Properly handles proxies and CDNs:
- Checks `HTTP_X_FORWARDED_FOR` header (for Cloudflare/proxies)
- Falls back to `REMOTE_ADDR`
- Logs actual client IP, not proxy IP

## Configuration

### Rate Limit Settings
Located in `src/middleware.py` - `PasswordResetRateLimitMiddleware`:

```python
self.max_attempts_per_ip = 5       # Max attempts per IP per window
self.max_attempts_per_email = 3    # Max attempts per email per window
self.window_minutes = 15           # Time window in minutes
self.lockout_minutes = 60          # Lockout duration after exceeding limits
```

### Token Expiration
Located in `Parliament/settings_postgres.py`:

```python
PASSWORD_RESET_TIMEOUT = 1800  # 30 minutes (in seconds)
```

### Cache Backend
Rate limiting uses Django's cache system:

```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'parliament-cache',
    }
}
```

**Note:** For production with multiple servers, consider using Redis or Memcached for shared cache.

## Monitoring

### View Recent Attempts
Check the logs for password reset activity:

```bash
# View all password reset attempts
tail -f logs/django_actions.log | grep "Password reset"

# View rate limit violations
grep "rate limit exceeded" logs/django_actions.log

# View lockout events
grep "locked out" logs/django_actions.log
```

### Admin Dashboard
Admins can view logs through the admin panel:
- Navigate to: `https://am-parliament.org/admin/view-logs/`
- Search for "Password reset" entries

## Testing Rate Limits

### Test IP-based rate limiting:
```bash
# Try 6 password reset requests in a row
for i in {1..6}; do
  curl -X POST http://localhost:8080/password-reset/ \
    -d "email=test@example.com" \
    --cookie-jar cookies.txt \
    --cookie cookies.txt
done
```

After the 5th attempt, you should see:
```
Too Many Requests
Too many password reset attempts. Please try again in 1 hour.
```

### Clear rate limits (for testing):
```bash
# Restart the Django server
# Rate limits are stored in memory cache and will clear on restart
```

## Production Recommendations

### 1. Use Redis for Cache (Multiple Servers)
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

### 2. Add CAPTCHA (Optional)
For extra protection, consider adding CAPTCHA to the password reset form:
- Google reCAPTCHA v3 (invisible)
- hCaptcha
- Cloudflare Turnstile

### 3. Monitor Suspicious Activity
Set up alerts for:
- Multiple lockout events from different IPs
- Repeated attempts against the same email
- Unusual geographic patterns

### 4. Adjust Rate Limits
Based on your user base, you may want to:
- Increase limits for trusted networks
- Decrease limits during high-risk periods
- Implement progressive delays instead of hard lockouts

## Security Best Practices

### Strong SECRET_KEY
Ensure `DJANGO_SECRET_KEY` in `.env` is:
- At least 50 characters long
- Random and unpredictable
- Never committed to version control
- Different between environments

### HTTPS Only
In production `.env`:
```bash
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

### Email Security
Use authenticated SMTP:
```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

## Troubleshooting

### "Too Many Requests" Error for Legitimate User
If a user is locked out legitimately:

**Option 1: Wait for expiration**
- IP lockouts expire after 60 minutes
- Email rate limits reset after 15 minutes

**Option 2: Manually clear cache (admin only)**
```bash
# SSH into production server
cd /var/www/Parliament-New
source venv/bin/activate

# Clear specific IP lockout
python3 manage.py shell
>>> from django.core.cache import cache
>>> cache.delete('password_reset_lockout_<IP_ADDRESS>')
>>> cache.delete('password_reset_ip_<IP_ADDRESS>')
```

**Option 3: Reset password via admin panel**
- Go to admin panel
- Navigate to user
- Use "Reset password via admin" feature

### Rate Limiting Not Working
Check that:
1. Middleware is enabled in `MIDDLEWARE` setting
2. Cache backend is configured
3. Django server has been restarted

### View Current Rate Limit Status
```bash
python3 manage.py shell

from django.core.cache import cache

# Check IP attempts
cache.get('password_reset_ip_192.168.1.100')

# Check if IP is locked out
cache.get('password_reset_lockout_192.168.1.100')

# Check email attempts
cache.get('password_reset_email_user@example.com')
```

## Summary

The password reset system now includes:
✅ IP-based rate limiting (5 per 15 min)
✅ Email-based rate limiting (3 per 15 min)
✅ Progressive lockouts (60 min after exceeding limits)
✅ Comprehensive logging of all attempts
✅ Cryptographically secure tokens
✅ Shortened 30-minute expiration window
✅ Single-use tokens
✅ No email enumeration
✅ Proxy/CDN support

These protections make brute force attacks impractical while maintaining usability for legitimate users.
