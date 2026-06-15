"""
Custom middleware for Parliament application
"""
from django.shortcuts import redirect, render
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponseForbidden, HttpResponseBadRequest, JsonResponse
from django.conf import settings
from src.utils.security_utils import get_client_ip as _get_client_ip
import logging
import re
import html
import secrets

logger = logging.getLogger('admin_actions')

# Compiled regex patterns for attack detection
SQL_INJECTION_PATTERNS = [
    re.compile(r"(\b(union|select|insert|update|delete|drop|create|alter|exec|execute)\b.*\b(from|into|table|database|where)\b)", re.IGNORECASE),
    re.compile(r"(@@|char\s*\(|nchar\s*\(|varchar\s*\(|nvarchar\s*\(|cast\s*\(|convert\s*\()", re.IGNORECASE),  # SQL functions (/* and */ removed — too broad; appear in normal text/code comments)
    re.compile(r"(\b(or|and)\b\s+\d+\s*=\s*\d+)", re.IGNORECASE),  # or 1=1, and 1=1
    re.compile(r"(\b(or|and)\b\s+'[^']*'\s*=\s*'[^']*')", re.IGNORECASE),  # or 'a'='a' — require quotes
    re.compile(r"(waitfor\s+delay|benchmark\s*\(|sleep\s*\()", re.IGNORECASE),  # Time-based injection
    re.compile(r"(information_schema|sys\.objects|sysobjects)", re.IGNORECASE),  # Schema probing
]

XSS_PATTERNS = [
    re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"(?:^|[\s<])on\w+\s*=\s*['\"]?[^'\"<>\s]", re.IGNORECASE),  # onclick= etc. — require HTML context
    re.compile(r"<iframe[^>]*>", re.IGNORECASE),
    re.compile(r"<object[^>]*>", re.IGNORECASE),
    re.compile(r"<embed[^>]*>", re.IGNORECASE),
    re.compile(r"<img[^>]*onerror\s*=", re.IGNORECASE),
    re.compile(r"url\s*\(\s*['\"]?\s*javascript:", re.IGNORECASE),
]

PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),  # ../
    re.compile(r"\.\.\\"),  # ..\
    re.compile(r"%2e%2e[/\\]", re.IGNORECASE),  # URL encoded
    re.compile(r"\.%00", re.IGNORECASE),  # Null byte
]

COMMAND_INJECTION_PATTERNS = [
    re.compile(r"[;&|`$]"),  # Shell metacharacters
    re.compile(r"\$\(.*\)"),  # Command substitution
    re.compile(r"`.*`"),  # Backtick execution
]


class ForcePasswordChangeMiddleware:
    """
    Middleware to force users to change password if force_password_change flag is set.
    Redirects authenticated users to the password change page if needed.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Paths that should be accessible even when password change is forced
        self.exempt_paths = [
            reverse('forced_password_change'),
            reverse('logout'),
            reverse('set_email'),
            '/admin/',
            '/onboarding/',
        ]

    def __call__(self, request):
        # Check if user is authenticated and needs to change password
        if hasattr(request, 'user') and request.user.is_authenticated and hasattr(request.user, 'force_password_change'):
            if request.user.force_password_change:
                # Allow access to certain paths
                if not any(request.path.startswith(path) for path in self.exempt_paths):
                    # Don't redirect if already on the password change page
                    if request.path != reverse('forced_password_change'):
                        return redirect('forced_password_change')

        response = self.get_response(request)
        return response


class QuarantineEnforcementMiddleware:
    """
    Middleware to enforce account quarantine on every request.
    Quarantined users are logged out immediately — login_view already blocks
    re-entry, so this closes the gap where a user quarantined mid-session
    could continue browsing until their session expired naturally.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_paths = [
            '/logout/',
            '/login/',
            '/static/',
        ]

    def __call__(self, request):
        if (
            hasattr(request, 'user')
            and request.user.is_authenticated
            and getattr(request.user, 'is_quarantined', False)
            and not any(request.path.startswith(p) for p in self.exempt_paths)
        ):
            from django.contrib.auth import logout
            logout(request)
            return redirect('/login/?quarantined=1')

        return self.get_response(request)


class PasswordResetRateLimitMiddleware:
    """
    Middleware to rate limit password reset requests and prevent brute force attacks.
    Tracks attempts by IP address and implements progressive delays.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Rate limit settings
        self.max_attempts_per_ip = 5  # Max attempts per IP per window
        self.max_attempts_per_email = 3  # Max attempts per email per window
        self.window_minutes = 15  # Time window in minutes
        self.lockout_minutes = 60  # Lockout duration after exceeding limits

    def __call__(self, request):
        # Only check password reset endpoints
        if request.path == '/password-reset/' and request.method == 'POST':
            ip_address = self.get_client_ip(request)

            # Check IP-based rate limit
            ip_key = f'password_reset_ip_{ip_address}'
            ip_attempts = cache.get(ip_key, 0)

            # Check if IP is locked out
            lockout_key = f'password_reset_lockout_{ip_address}'
            if cache.get(lockout_key):
                logger.warning(
                    f'Password reset blocked: IP {ip_address} is locked out due to too many attempts'
                )
                return render(request, '403.html', {
                    'reason': 'Too many password reset attempts. Please try again later.'
                }, status=403)

            # Check if IP has exceeded rate limit
            if ip_attempts >= self.max_attempts_per_ip:
                logger.warning(
                    f'Password reset rate limit exceeded for IP {ip_address}. '
                    f'Attempts: {ip_attempts}'
                )
                # Lock out the IP
                cache.set(lockout_key, True, self.lockout_minutes * 60)
                return render(request, '403.html', {
                    'reason': 'Too many password reset attempts. Please try again in 1 hour.'
                }, status=403)

            # Increment IP attempt counter
            cache.set(ip_key, ip_attempts + 1, self.window_minutes * 60)

            # Check email-based rate limit if email is provided
            email = request.POST.get('email', '').strip().lower()
            if email:
                email_key = f'password_reset_email_{email}'
                email_attempts = cache.get(email_key, 0)

                if email_attempts >= self.max_attempts_per_email:
                    logger.warning(
                        f'Password reset rate limit exceeded for email {email} from IP {ip_address}'
                    )

                # Increment email attempt counter (once, regardless of limit state)
                cache.set(email_key, email_attempts + 1, self.window_minutes * 60)

                # Log the attempt
                logger.info(
                    f'Password reset requested for email {email} from IP {ip_address}. '
                    f'IP attempts: {ip_attempts + 1}/{self.max_attempts_per_ip}, '
                    f'Email attempts: {email_attempts + 1}/{self.max_attempts_per_email}'
                )

        response = self.get_response(request)
        return response

    def get_client_ip(self, request):
        """Get the client's IP address from the request."""
        return _get_client_ip(request) or 'unknown'


class LoginRateLimitMiddleware:
    """
    Middleware to rate limit login attempts and prevent brute force attacks.
    Tracks both IP-based and username-based attempts with progressive lockouts.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Rate limit settings
        self.max_attempts_per_ip = 10  # Max login attempts per IP per window
        self.max_attempts_per_username = 5  # Max attempts per username per window
        self.window_minutes = 15  # Time window in minutes
        self.lockout_minutes = 30  # Lockout duration after exceeding limits

    def _is_ip_whitelisted(self, ip_address):
        """Check if an IP is on the active whitelist (DB query cached briefly)."""
        cache_key = f'ip_whitelist_{ip_address}'
        result = cache.get(cache_key)
        if result is None:
            from src.models import IPWhitelist
            result = IPWhitelist.objects.filter(ip_address=ip_address, is_active=True).exists()
            cache.set(cache_key, result, 60)  # cache for 60 seconds
        return result

    def __call__(self, request):
        # Only check login endpoints
        if (request.path == '/login/' or request.path == '/accounts/login/') and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            username = request.POST.get('username', '').strip().lower()

            # Whitelisted IPs bypass all rate limiting
            if self._is_ip_whitelisted(ip_address):
                return self.get_response(request)

            # Check IP-based rate limit
            ip_key = f'login_attempts_ip_{ip_address}'
            ip_attempts = cache.get(ip_key, 0)

            # Check if IP is locked out
            ip_lockout_key = f'login_lockout_ip_{ip_address}'
            if cache.get(ip_lockout_key):
                logger.warning(
                    f'Login blocked: IP {ip_address} is locked out due to too many attempts'
                )
                return redirect(f'/login/?rl={self.lockout_minutes}&reason=ip')

            # Check if IP has exceeded rate limit
            if ip_attempts >= self.max_attempts_per_ip:
                logger.warning(
                    f'Login rate limit exceeded for IP {ip_address}. Attempts: {ip_attempts}'
                )
                # Lock out the IP
                cache.set(ip_lockout_key, True, self.lockout_minutes * 60)
                # Persist lockout to DB for admin visibility
                try:
                    from src.models import LoginLockout
                    from django.utils import timezone as tz
                    from datetime import timedelta as td
                    expires = tz.now() + td(minutes=self.lockout_minutes)
                    LoginLockout.objects.create(
                        ip_address=ip_address,
                        source='middleware_ip',
                        expires_at=expires,
                    )
                except Exception:
                    pass
                return redirect(f'/login/?rl={self.lockout_minutes}&reason=ip')

            # Check username-based rate limit if username is provided
            if username:
                username_key = f'login_attempts_user_{username}'
                username_attempts = cache.get(username_key, 0)
                username_lockout_key = f'login_lockout_user_{username}'

                # Check if username is locked out
                if cache.get(username_lockout_key):
                    logger.warning(
                        f'Login blocked: Username {username} is locked out. Attempt from IP {ip_address}'
                    )
                    return redirect(f'/login/?rl={self.lockout_minutes}&reason=user')

                if username_attempts >= self.max_attempts_per_username:
                    logger.warning(
                        f'Login rate limit exceeded for username {username} from IP {ip_address}. '
                        f'Attempts: {username_attempts}'
                    )
                    # Lock out the username
                    cache.set(username_lockout_key, True, self.lockout_minutes * 60)
                    # Persist lockout to DB for admin visibility
                    try:
                        from src.models import LoginLockout
                        from django.utils import timezone as tz
                        from datetime import timedelta as td
                        expires = tz.now() + td(minutes=self.lockout_minutes)
                        LoginLockout.objects.create(
                            ip_address=ip_address,
                            username=username,
                            source='middleware_user',
                            expires_at=expires,
                        )
                    except Exception:
                        pass

        # Passkey authentication endpoint — same IP-based lockout as /login/.
        # No username is available pre-request, so IP tracking only.
        if request.path == '/accounts/passkeys/authenticate/complete/' and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            if not self._is_ip_whitelisted(ip_address):
                ip_lockout_key = f'login_lockout_ip_{ip_address}'
                if cache.get(ip_lockout_key):
                    logger.warning(f'Passkey auth blocked: IP {ip_address} is locked out')
                    return JsonResponse({'error': 'Too many failed attempts. Try again later.'}, status=429)
                ip_key = f'login_attempts_ip_{ip_address}'
                if cache.get(ip_key, 0) >= self.max_attempts_per_ip:
                    cache.set(ip_lockout_key, True, self.lockout_minutes * 60)
                    logger.warning(f'Passkey auth rate limit hit for IP {ip_address}, locking out')
                    return JsonResponse({'error': 'Too many failed attempts. Try again later.'}, status=429)

        response = self.get_response(request)

        # After response, track failed login attempts.
        # Status codes: 200 = failed (form re-rendered), 302 = success (redirect after login).
        # Any other status (e.g. 403 from this middleware itself, or 302 from a lockout redirect)
        # must not touch counters — clearing on a self-generated 403 would undo the lockout.
        if (request.path == '/login/' or request.path == '/accounts/login/') and request.method == 'POST':
            if response.status_code == 200:
                # Failed login — form was re-rendered with errors
                ip_address = self.get_client_ip(request)
                username = request.POST.get('username', '').strip().lower()

                # Increment IP attempt counter
                ip_key = f'login_attempts_ip_{ip_address}'
                ip_attempts = cache.get(ip_key, 0)
                cache.set(ip_key, ip_attempts + 1, self.window_minutes * 60)

                # Increment username attempt counter
                if username:
                    username_key = f'login_attempts_user_{username}'
                    username_attempts = cache.get(username_key, 0)
                    cache.set(username_key, username_attempts + 1, self.window_minutes * 60)

                    logger.warning(
                        f'Failed login attempt for username "{username}" from IP {ip_address}. '
                        f'IP attempts: {ip_attempts + 1}/{self.max_attempts_per_ip}, '
                        f'Username attempts: {username_attempts + 1}/{self.max_attempts_per_username}'
                    )
            elif response.status_code == 302 and 'rl=' not in response.get('Location', ''):
                # Successful login redirect (not a rate-limit redirect) — clear attempt counters
                ip_address = self.get_client_ip(request)
                username = request.POST.get('username', '').strip().lower()

                if username:
                    cache.delete(f'login_attempts_ip_{ip_address}')
                    cache.delete(f'login_attempts_user_{username}')
                    cache.delete(f'login_lockout_ip_{ip_address}')
                    cache.delete(f'login_lockout_user_{username}')
            # else: blocked by this middleware (lockout redirect) — leave counters alone

        # Passkey auth post-response: increment on failure, clear on success
        if request.path == '/accounts/passkeys/authenticate/complete/' and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            ip_key = f'login_attempts_ip_{ip_address}'
            if response.status_code >= 400:
                ip_attempts = cache.get(ip_key, 0) + 1
                cache.set(ip_key, ip_attempts, self.window_minutes * 60)
                logger.warning(
                    f'Failed passkey auth attempt from IP {ip_address}. '
                    f'IP attempts: {ip_attempts}/{self.max_attempts_per_ip}'
                )
            elif response.status_code == 200:
                cache.delete(ip_key)
                cache.delete(f'login_lockout_ip_{ip_address}')

        return response

    def get_client_ip(self, request):
        """Get the client's IP address from the request."""
        return _get_client_ip(request) or 'unknown'


class InputSanitizationMiddleware:
    """
    Middleware to detect and log potential SQL injection, XSS, and other attacks.
    Also adds security headers to all responses.

    Note: Django's ORM already protects against SQL injection via parameterized queries.
    Django's template system already escapes output to prevent XSS.
    This middleware adds an extra layer of defense by:
    1. Detecting and logging attack attempts for security monitoring
    2. Blocking obviously malicious requests
    3. Adding security headers
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Paths to skip checking (e.g., admin that has its own handling,
        # or officer pages that accept rich HTML content with legitimate CSS/HTML)
        self.skip_paths = [
            '/admin/',
            '/static/',
            '/media/',
            '/login/',               # Password fields should never be scanned
            '/accounts/login/',      # Password fields should never be scanned
            '/officers/edit-landing-page/',  # Rich HTML editor — CSS semicolons trigger false positives
            '/contact/submit/',              # Public contact form — free-text messages trigger false positives
            '/legislation/',                 # Officer notes are free-text and may contain SQL-like patterns
        ]
        # Maximum input length before truncating for logging
        self.max_log_length = 500
        # Fields that should never be scanned (tokens, internal fields)
        self.skip_fields = {'csrfmiddlewaretoken', 'next'}
        # Minimum value length to bother scanning — short values can't contain real exploits
        self.min_scan_length = 8

    def __call__(self, request):
        # Generate a per-request CSP nonce. Must happen before get_response() so
        # templates can reference {{ request.csp_nonce }} during rendering.
        request.csp_nonce = secrets.token_urlsafe(16)

        ip_address = self.get_client_ip(request)

        # Skip checking for static files and certain paths
        if any(request.path.startswith(path) for path in self.skip_paths):
            response = self.get_response(request)
            return self.add_security_headers(response, request.csp_nonce, path=request.path)

        # Enforce IPBlacklist for all requests (cache result for 5 minutes to avoid per-request DB hits)
        blacklist_cache_key = f'ip_blacklisted_{ip_address}'
        is_blacklisted = cache.get(blacklist_cache_key)
        if is_blacklisted is None:
            try:
                from src.models import IPBlacklist
                is_blacklisted = IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists()
            except Exception:
                is_blacklisted = False
            cache.set(blacklist_cache_key, is_blacklisted, 300)
        if is_blacklisted:
            logger.warning(
                f"BLACKLISTED_IP_BLOCKED: {ip_address} attempted {request.method} {request.path}"
            )
            return render(request, '403.html', {
                'reason': 'Your IP address has been blocked. Contact an administrator if you believe this is an error.'
            }, status=403)

        # Authenticated users are already past auth/CSRF/session checks.
        # Django's ORM parameterizes all queries and templates auto-escape output,
        # so pattern scanning adds no real protection for logged-in members and
        # is the primary source of false positives on free-text form fields.
        if hasattr(request, 'user') and request.user.is_authenticated:
            response = self.get_response(request)
            return self.add_security_headers(response, request.csp_nonce, path=request.path)

        # Check all input sources for malicious patterns
        attack_detected = False
        attack_type = None
        attack_pattern = None
        attack_payload = None

        # Check GET parameters
        for key, value in request.GET.items():
            if key in self.skip_fields or len(value) < self.min_scan_length:
                continue
            result = self.check_for_attacks(value)
            if result:
                attack_detected = True
                attack_type = result['type']
                attack_pattern = result['pattern']
                attack_payload = f"GET[{key}]={self.truncate(value)}"
                break

        # Check POST parameters (only for form data, not file uploads)
        if not attack_detected and request.method == 'POST':
            try:
                for key, value in request.POST.items():
                    if key in self.skip_fields or not isinstance(value, str) or len(value) < self.min_scan_length:
                        continue
                    result = self.check_for_attacks(value)
                    if result:
                        attack_detected = True
                        attack_type = result['type']
                        attack_pattern = result['pattern']
                        attack_payload = f"POST[{key}]={self.truncate(value)}"
                        break
            except Exception:
                pass  # Skip if POST data can't be read

        # Check URL path
        if not attack_detected:
            result = self.check_for_attacks(request.path)
            if result:
                attack_detected = True
                attack_type = result['type']
                attack_pattern = result['pattern']
                attack_payload = f"PATH={self.truncate(request.path)}"

        # If attack detected, log and potentially block
        if attack_detected:
            user_info = (f"User: {request.user.username}"
                         if hasattr(request, 'user') and request.user.is_authenticated
                         else "Unauthenticated")
            logger.warning(
                f"ATTACK DETECTED [{attack_type}]: {attack_payload} | "
                f"Pattern: {attack_pattern} | "
                f"IP: {ip_address} | {user_info} | "
                f"Path: {request.path} | UA: {request.META.get('HTTP_USER_AGENT', 'unknown')[:100]}"
            )

            # Track attack attempts per IP
            attack_count_key = f'attack_attempts_{ip_address}'
            attack_count = cache.get(attack_count_key, 0) + 1
            cache.set(attack_count_key, attack_count, 3600)  # 1 hour window

            # Block if too many attack attempts
            if attack_count >= 10:
                logger.critical(
                    f"BLOCKING IP {ip_address}: {attack_count} attack attempts in 1 hour. "
                    f"Latest: {attack_type}"
                )

                # Send security alert for critical attack threshold
                try:
                    from src.security_notifications import alert_attack_blocked
                    alert_attack_blocked(
                        ip_address=ip_address,
                        attack_count=attack_count,
                        attack_type=attack_type,
                        details=f"Payload: {attack_payload}"
                    )
                except Exception as e:
                    logger.error(f"Failed to send attack alert: {e}")

                # Auto-quarantine authenticated user if attacks persist
                if hasattr(request, 'user') and request.user.is_authenticated and attack_count >= 20:
                    try:
                        from src.models import QuarantinedAccount
                        from src.security_notifications import alert_account_quarantined
                        if not request.user.is_quarantined:
                            QuarantinedAccount.quarantine_user(
                                user=request.user,
                                ip_address=ip_address,
                                reason=f"Auto-quarantined: {attack_count} attack attempts detected. Latest: {attack_type}"
                            )
                            alert_account_quarantined(
                                user=request.user,
                                ip_address=ip_address,
                                reason=f"Automated quarantine due to {attack_count} attack attempts",
                                is_auto=True
                            )
                    except Exception as e:
                        logger.error(f"Failed to auto-quarantine user: {e}")

                return render(request, '403.html', {
                    'reason': 'Your request has been blocked due to suspicious activity. Contact an administrator if you believe this is an error.'
                }, status=403)

        response = self.get_response(request)
        return self.add_security_headers(response, request.csp_nonce, path=request.path)

    def check_for_attacks(self, value):
        """Check a value for various attack patterns."""
        if not value or not isinstance(value, str):
            return None

        # Check for SQL injection
        for pattern in SQL_INJECTION_PATTERNS:
            if pattern.search(value):
                return {'type': 'SQL_INJECTION', 'pattern': pattern.pattern}

        # Check for XSS
        for pattern in XSS_PATTERNS:
            if pattern.search(value):
                return {'type': 'XSS', 'pattern': pattern.pattern}

        # Check for path traversal
        for pattern in PATH_TRAVERSAL_PATTERNS:
            if pattern.search(value):
                return {'type': 'PATH_TRAVERSAL', 'pattern': pattern.pattern}

        # Check for command injection (only for certain fields)
        # Be careful not to block legitimate uses
        # for pattern in COMMAND_INJECTION_PATTERNS:
        #     if pattern.search(value):
        #         return {'type': 'COMMAND_INJECTION', 'pattern': pattern.pattern}

        return None

    def add_security_headers(self, response, csp_nonce=None, path=None):
        """Add security headers to the response."""
        # Prevent MIME type sniffing
        response['X-Content-Type-Options'] = 'nosniff'

        # Prevent clickjacking
        response['X-Frame-Options'] = 'SAMEORIGIN'

        # Enable XSS filter in browsers (legacy but still useful)
        response['X-XSS-Protection'] = '1; mode=block'

        # Referrer policy
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # Permissions policy (limit access to sensitive browser features)
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'

        # Content Security Policy
        #
        # script-src uses a per-request nonce with NO 'unsafe-inline'.
        # Every inline <script> tag in templates carries nonce="{{ request.csp_nonce }}"
        # and every inline event handler (onclick=, onchange=, etc.) has been removed
        # from all templates in favour of addEventListener calls. Any injected script
        # without the correct nonce is blocked by the browser before it executes.
        #
        # style-src keeps 'unsafe-inline' because inline style= attributes are used
        # throughout templates (Alpine.js, dynamic widths, etc.) and cannot be nonced.
        # Inline styles can't execute code directly, so this is an acceptable trade-off.
        #
        # Cloudflare injects beacon.min.js for Web Analytics; allow its domain
        # when BEHIND_CLOUDFLARE is enabled. CSP violations are reported to
        # /csp-report/ and logged to SecurityNotificationLog for review.
        if not getattr(settings, 'DEBUG', False):
            behind_cf = getattr(settings, 'BEHIND_CLOUDFLARE', False)
            cf_beacon = ' https://static.cloudflareinsights.com' if behind_cf else ''
            # object-src / worker-src: locked to 'none' — no plugins or web workers.
            # upgrade-insecure-requests: browser upgrades any accidental http:// refs.
            nonce_val = f"'nonce-{csp_nonce}'" if csp_nonce else ''
            csp_parts = [
                "default-src 'self'",
                f"script-src 'self' {nonce_val}{cf_beacon}".strip(),
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: https:",
                "font-src 'self' data:",
                f"connect-src 'self'{cf_beacon}",
                "frame-ancestors 'self'",
                "form-action 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "worker-src 'self'",
                "upgrade-insecure-requests",
                "report-uri /csp-report/",
            ]
            response['Content-Security-Policy'] = '; '.join(csp_parts)

        return response

    def truncate(self, value, max_length=None):
        """Truncate a value for safe logging."""
        max_length = max_length or self.max_log_length
        if len(value) > max_length:
            return value[:max_length] + '...[truncated]'
        return value

    def get_client_ip(self, request):
        """Get the client's IP address from the request."""
        return _get_client_ip(request) or 'unknown'


class AdminAccessMonitoringMiddleware:
    """
    Middleware to monitor and log all admin panel access attempts.
    Provides security audit trail for administrative actions.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Monitor admin panel access
        if request.path.startswith('/admin/'):
            ip_address = self.get_client_ip(request)

            # Log admin access attempts
            if hasattr(request, 'user') and request.user.is_authenticated:
                if hasattr(request.user, 'is_admin') and request.user.is_admin:
                    # Log successful admin access
                    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                        logger.info(
                            f"ADMIN ACTION: User '{request.user.username}' "
                            f"({request.method} {request.path}) from IP {ip_address}"
                        )
                else:
                    # Log unauthorized admin access attempt
                    logger.warning(
                        f"ADMIN ACCESS DENIED: Non-admin user '{request.user.username}' "
                        f"attempted to access {request.path} from IP {ip_address}"
                    )
            else:
                # Log unauthenticated admin access attempt
                if request.method == 'POST':  # Only log POST to avoid spam from page loads
                    logger.warning(
                        f"ADMIN LOGIN ATTEMPT: Unauthenticated access to {request.path} "
                        f"from IP {ip_address}"
                    )

        response = self.get_response(request)
        return response

    def get_client_ip(self, request):
        """Get the client's IP address from the request."""
        return _get_client_ip(request) or 'unknown'
