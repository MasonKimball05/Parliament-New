from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from ..models import IPBlacklist, UserSession
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
import logging


# Rate limiting settings
MAX_LOGIN_ATTEMPTS = 5  # Maximum failed attempts before lockout
LOCKOUT_DURATION = 15 * 60  # Lockout duration in seconds (15 minutes)
ATTEMPT_WINDOW = 15 * 60  # Window to count attempts in seconds (15 minutes)


def get_client_ip(request):
    """Get the client's IP address from the request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return ip


def get_rate_limit_key(ip_address):
    """Generate cache key for rate limiting."""
    return f"login_attempts_{ip_address}"


def get_lockout_key(ip_address):
    """Generate cache key for lockout status."""
    return f"login_lockout_{ip_address}"


def is_rate_limited(ip_address):
    """Check if an IP is currently rate limited."""
    lockout_key = get_lockout_key(ip_address)
    lockout_until = cache.get(lockout_key)
    if lockout_until:
        return True, lockout_until
    return False, None


def record_failed_attempt(ip_address):
    """
    Record a failed login attempt and check if lockout should be triggered.
    Returns (is_locked_out, attempts_remaining, lockout_until)
    """
    attempts_key = get_rate_limit_key(ip_address)
    lockout_key = get_lockout_key(ip_address)

    # Get current attempt count
    attempts = cache.get(attempts_key, 0)
    attempts += 1

    # Store updated count with expiry
    cache.set(attempts_key, attempts, ATTEMPT_WINDOW)

    if attempts >= MAX_LOGIN_ATTEMPTS:
        # Trigger lockout
        lockout_until = timezone.now() + timedelta(seconds=LOCKOUT_DURATION)
        cache.set(lockout_key, lockout_until, LOCKOUT_DURATION)
        # Clear attempts counter
        cache.delete(attempts_key)
        return True, 0, lockout_until

    return False, MAX_LOGIN_ATTEMPTS - attempts, None


def clear_failed_attempts(ip_address):
    """Clear failed attempts after successful login."""
    cache.delete(get_rate_limit_key(ip_address))
    cache.delete(get_lockout_key(ip_address))


def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')[:200]

        # Check rate limiting first
        is_locked, lockout_until = is_rate_limited(ip_address)
        if is_locked:
            remaining = (lockout_until - timezone.now()).seconds // 60 + 1
            security_logger = logging.getLogger('admin_actions')
            security_logger.warning(
                f"RATE LIMITED: IP {ip_address} attempted login while locked out. "
                f"Lockout expires in {remaining} minutes."
            )
            messages.error(
                request,
                f"Too many failed login attempts. Please try again in {remaining} minute{'s' if remaining != 1 else ''}."
            )
            return redirect('login')

        # Check if IP is blacklisted
        blacklist_entry = IPBlacklist.objects.filter(
            ip_address=ip_address,
            is_active=True
        ).first()

        if blacklist_entry:
            # Check if blacklist has expired
            if blacklist_entry.expires_at and blacklist_entry.expires_at < timezone.now():
                # Blacklist expired, deactivate it
                blacklist_entry.is_active = False
                blacklist_entry.save()
            else:
                # IP is actively blacklisted, update block count and deny access
                blacklist_entry.block_count += 1
                blacklist_entry.last_blocked = timezone.now()
                blacklist_entry.save()

                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"BLOCKED LOGIN: Blacklisted IP {ip_address} attempted login as '{username}'. "
                    f"Reason: {blacklist_entry.reason}"
                )

                messages.error(
                    request,
                    "Access denied. Your IP address has been blocked. Please contact an administrator if you believe this is an error."
                )
                return redirect('login')

        if not username or not password:
            messages.error(request, "Both username and password are required.")
            security_logger = logging.getLogger('admin_actions')
            security_logger.warning(
                f"Login attempt with missing credentials from IP {ip_address}"
            )
            return redirect('login')

        # Use Django's built-in authenticate method for secure password checking
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_active:
                login(request, user)

                # Clear any failed attempt counters on successful login
                clear_failed_attempts(ip_address)

                # Create session record for session management
                UserSession.create_or_update_session(user, request)

                # Log successful login with IP and user agent
                logger = logging.getLogger('function_calls')
                logger.info(
                    f"Successful login: {user.name} ({user.member_type}) (user_id={user.user_id}) "
                    f"from IP {ip_address}"
                )

                # Also log to admin_actions for security audit
                security_logger = logging.getLogger('admin_actions')
                security_logger.info(
                    f"LOGIN SUCCESS: User '{username}' (ID: {user.user_id}) from IP {ip_address}"
                )

                messages.success(request, f"Welcome, {user.get_display_name() if hasattr(user, 'get_display_name') else user.name}!")

                next_url = request.GET.get('next', 'home')

                return redirect(next_url)
            else:
                # Disabled account also counts as failed attempt
                is_locked, remaining, lockout_until = record_failed_attempt(ip_address)

                messages.error(request, "This account has been disabled.")
                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"LOGIN FAILED: Attempt to access disabled account '{username}' from IP {ip_address}"
                )

                if is_locked:
                    messages.warning(
                        request,
                        f"Too many failed attempts. You are now locked out for {LOCKOUT_DURATION // 60} minutes."
                    )

                return redirect('login')
        else:
            # Record failed attempt and check for lockout
            is_locked, remaining, lockout_until = record_failed_attempt(ip_address)

            if is_locked:
                messages.error(
                    request,
                    f"Too many failed login attempts. You are now locked out for {LOCKOUT_DURATION // 60} minutes."
                )
                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"RATE LIMIT TRIGGERED: IP {ip_address} locked out after {MAX_LOGIN_ATTEMPTS} failed attempts. "
                    f"Username attempted: '{username}'"
                )
            else:
                messages.error(request, "Invalid username or password.")
                if remaining <= 2:
                    messages.warning(request, f"Warning: {remaining} attempt{'s' if remaining != 1 else ''} remaining before lockout.")

            # Log failed login attempt
            security_logger = logging.getLogger('admin_actions')
            security_logger.warning(
                f"LOGIN FAILED: Invalid credentials for username '{username}' from IP {ip_address} "
                f"(Attempts remaining: {remaining})"
            )

            return redirect('login')

    return render(request, 'registration/login.html')