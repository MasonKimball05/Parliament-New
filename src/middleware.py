"""
Custom middleware for Parliament application
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.contrib import messages
import logging

logger = logging.getLogger('admin_actions')


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
