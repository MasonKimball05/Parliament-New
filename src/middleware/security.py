"""
Custom middleware for Parliament application
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponseForbidden, HttpResponseBadRequest
from django.contrib import messages
from django.conf import settings
import logging
import re
import html

logger = logging.getLogger('admin_actions')

# Compiled regex patterns for attack detection
SQL_INJECTION_PATTERNS = [
    re.compile(r"(\b(union|select|insert|update|delete|drop|create|alter|exec|execute)\b.*\b(from|into|table|database|where)\b)", re.IGNORECASE),
    re.compile(r"(--|;|/\*|\*/|@@|@|char\(|nchar\(|varchar\(|nvarchar\(|cast\(|convert\()", re.IGNORECASE),
    re.compile(r"(\b(or|and)\b\s+\d+\s*=\s*\d+)", re.IGNORECASE),  # or 1=1, and 1=1
    re.compile(r"(\b(or|and)\b\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?)", re.IGNORECASE),  # or 'a'='a'
    re.compile(r"(waitfor\s+delay|benchmark\s*\(|sleep\s*\()", re.IGNORECASE),  # Time-based injection
    re.compile(r"(information_schema|sys\.objects|sysobjects)", re.IGNORECASE),  # Schema probing
]

XSS_PATTERNS = [
    re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"on\w+\s*=\s*['\"]?[^'\"]*['\"]?", re.IGNORECASE),  # onclick=, onerror=, etc.
    re.compile(r"<iframe[^>]*>", re.IGNORECASE),
    re.compile(r"<object[^>]*>", re.IGNORECASE),
    re.compile(r"<embed[^>]*>", re.IGNORECASE),
    re.compile(r"<link[^>]*>", re.IGNORECASE),
    re.compile(r"<img[^>]*onerror\s*=", re.IGNORECASE),
    re.compile(r"expression\s*\(", re.IGNORECASE),  # CSS expression
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
            '/admin/',  # Allow admin access
        ]

    def __call__(self, request):
        # Check if user is authenticated and needs to change password
        if request.user.is_authenticated and hasattr(request.user, 'force_password_change'):
            if request.user.force_password_change:
                # Allow access to certain paths
                if not any(request.path.startswith(path) for path in self.exempt_paths):
                    # Don't redirect if already on the password change page
                    if request.path != reverse('forced_password_change'):
                        return redirect('forced_password_change')

        response = self.get_response(request)
        return response


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
                return HttpResponseForbidden(
                    '<html><body>'
                    '<h1>Too Many Requests</h1>'
                    '<p>Too many password reset attempts. Please try again later.</p>'
                    '<p>If you need immediate assistance, please contact an administrator.</p>'
                    '</body></html>'
                )

            # Check if IP has exceeded rate limit
            if ip_attempts >= self.max_attempts_per_ip:
                logger.warning(
                    f'Password reset rate limit exceeded for IP {ip_address}. '
                    f'Attempts: {ip_attempts}'
                )
                # Lock out the IP
                cache.set(lockout_key, True, self.lockout_minutes * 60)
                return HttpResponseForbidden(
                    '<html><body>'
                    '<h1>Too Many Requests</h1>'
                    '<p>Too many password reset attempts. Please try again in 1 hour.</p>'
                    '</body></html>'
                )

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
                    # Don't reveal that the email exists, just slow them down
                    cache.set(email_key, email_attempts + 1, self.window_minutes * 60)

                # Increment email attempt counter
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
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip


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

    def __call__(self, request):
        # Only check login endpoints
        if (request.path == '/login/' or request.path == '/accounts/login/') and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            username = request.POST.get('username', '').strip().lower()

            # Check IP-based rate limit
            ip_key = f'login_attempts_ip_{ip_address}'
            ip_attempts = cache.get(ip_key, 0)

            # Check if IP is locked out
            ip_lockout_key = f'login_lockout_ip_{ip_address}'
            if cache.get(ip_lockout_key):
                logger.warning(
                    f'Login blocked: IP {ip_address} is locked out due to too many attempts'
                )
                return HttpResponseForbidden(
                    '<html><body style="font-family: sans-serif; max-width: 600px; margin: 100px auto; padding: 20px;">'
                    '<h1 style="color: #dc2626;">Account Temporarily Locked</h1>'
                    '<p>Too many failed login attempts from your IP address.</p>'
                    '<p>Please try again in 30 minutes, or contact an administrator if you need immediate access.</p>'
                    '<p><a href="/login/" style="color: #2563eb;">← Back to Login</a></p>'
                    '</body></html>'
                )

            # Check if IP has exceeded rate limit
            if ip_attempts >= self.max_attempts_per_ip:
                logger.warning(
                    f'Login rate limit exceeded for IP {ip_address}. Attempts: {ip_attempts}'
                )
                # Lock out the IP
                cache.set(ip_lockout_key, True, self.lockout_minutes * 60)
                return HttpResponseForbidden(
                    '<html><body style="font-family: sans-serif; max-width: 600px; margin: 100px auto; padding: 20px;">'
                    '<h1 style="color: #dc2626;">Too Many Login Attempts</h1>'
                    '<p>Your IP address has been temporarily blocked due to excessive failed login attempts.</p>'
                    '<p>Please try again in 30 minutes.</p>'
                    '<p><a href="/login/" style="color: #2563eb;">← Back to Login</a></p>'
                    '</body></html>'
                )

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
                    # Don't reveal if username exists, use generic message
                    return HttpResponseForbidden(
                        '<html><body style="font-family: sans-serif; max-width: 600px; margin: 100px auto; padding: 20px;">'
                        '<h1 style="color: #dc2626;">Account Temporarily Locked</h1>'
                        '<p>This account has been temporarily locked due to multiple failed login attempts.</p>'
                        '<p>Please try again in 30 minutes, or use the "Forgot Password" link to reset your password.</p>'
                        '<p><a href="/login/" style="color: #2563eb;">← Back to Login</a></p>'
                        '<p><a href="/password-reset/" style="color: #2563eb;">Reset Password</a></p>'
                        '</body></html>'
                    )

                if username_attempts >= self.max_attempts_per_username:
                    logger.warning(
                        f'Login rate limit exceeded for username {username} from IP {ip_address}. '
                        f'Attempts: {username_attempts}'
                    )
                    # Lock out the username
                    cache.set(username_lockout_key, True, self.lockout_minutes * 60)

        response = self.get_response(request)

        # After response, track failed login attempts
        if (request.path == '/login/' or request.path == '/accounts/login/') and request.method == 'POST':
            # Check if login failed by looking for error messages
            storage = messages.get_messages(request)
            has_error = any('Invalid' in str(msg) or 'disabled' in str(msg) for msg in storage)

            if has_error:
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
            else:
                # Successful login - clear attempt counters
                ip_address = self.get_client_ip(request)
                username = request.POST.get('username', '').strip().lower()

                if username:
                    cache.delete(f'login_attempts_ip_{ip_address}')
                    cache.delete(f'login_attempts_user_{username}')
                    cache.delete(f'login_lockout_ip_{ip_address}')
                    cache.delete(f'login_lockout_user_{username}')

        return response

    def get_client_ip(self, request):
        """Get the client's IP address from the request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip


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
        # Paths to skip checking (e.g., admin that has its own handling)
        self.skip_paths = ['/admin/', '/static/', '/media/']
        # Maximum input length before truncating for logging
        self.max_log_length = 500

    def __call__(self, request):
        ip_address = self.get_client_ip(request)

        # Skip checking for static files and certain paths
        if any(request.path.startswith(path) for path in self.skip_paths):
            response = self.get_response(request)
            return self.add_security_headers(response)

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
            return HttpResponseForbidden(
                '<html><body><h1>403 Forbidden</h1></body></html>',
                content_type='text/html'
            )

        # Check all input sources for malicious patterns
        attack_detected = False
        attack_type = None
        attack_payload = None

        # Check GET parameters
        for key, value in request.GET.items():
            result = self.check_for_attacks(value)
            if result:
                attack_detected = True
                attack_type = result['type']
                attack_payload = f"GET[{key}]={self.truncate(value)}"
                break

        # Check POST parameters (only for form data, not file uploads)
        if not attack_detected and request.method == 'POST':
            try:
                for key, value in request.POST.items():
                    if isinstance(value, str):
                        result = self.check_for_attacks(value)
                        if result:
                            attack_detected = True
                            attack_type = result['type']
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
                attack_payload = f"PATH={self.truncate(request.path)}"

        # If attack detected, log and potentially block
        if attack_detected:
            user_info = f"User: {request.user.username}" if request.user.is_authenticated else "Unauthenticated"
            logger.warning(
                f"ATTACK DETECTED [{attack_type}]: {attack_payload} | "
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
                if request.user.is_authenticated and attack_count >= 20:
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

                return HttpResponseForbidden(
                    '<html><body style="font-family: sans-serif; max-width: 600px; margin: 100px auto; padding: 20px;">'
                    '<h1 style="color: #dc2626;">Access Denied</h1>'
                    '<p>Your request has been blocked due to suspicious activity.</p>'
                    '<p>If you believe this is an error, please contact the administrator.</p>'
                    '</body></html>'
                )

        response = self.get_response(request)
        return self.add_security_headers(response)

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

    def add_security_headers(self, response):
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

        # Content Security Policy (adjust based on your needs)
        # Note: Tailwind CDN is used, so we need to allow it
        if not getattr(settings, 'DEBUG', False):
            csp_parts = [
                "default-src 'self'",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com",
                "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com",
                "img-src 'self' data: https:",
                "font-src 'self' data:",
                "connect-src 'self'",
                "frame-ancestors 'self'",
                "form-action 'self'",
                "base-uri 'self'",
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
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip


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
            if request.user.is_authenticated:
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
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip
