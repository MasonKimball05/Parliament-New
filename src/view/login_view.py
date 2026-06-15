from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.utils.http import url_has_allowed_host_and_scheme
from ..models import IPBlacklist, IPWhitelist, UserSession, LoginLockout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from django.contrib.auth import get_user_model
from src.geo_utils import is_foreign_ip
from src.utils.security_utils import get_client_ip, run_post_auth_pipeline
from src.security_notifications import send_watch_flag_alert
import logging


# Rate limiting settings
MAX_LOGIN_ATTEMPTS = 5       # Per-IP lockout threshold
LOCKOUT_DURATION = 15 * 60  # Lockout duration in seconds (15 minutes)
ATTEMPT_WINDOW = 15 * 60    # Window to count attempts (15 minutes)

# Per-account lockout (catches distributed/IP-rotating attacks on one username)
MAX_ACCOUNT_ATTEMPTS = 10
ACCOUNT_LOCKOUT_DURATION = 15 * 60


def get_rate_limit_key(ip_address):
    """Generate cache key for rate limiting."""
    return f"login_attempts_{ip_address}"


def get_lockout_key(ip_address):
    """Generate cache key for lockout status."""
    return f"login_lockout_{ip_address}"


def is_ip_whitelisted(ip_address):
    """Check if an IP is on the active whitelist."""
    return IPWhitelist.objects.filter(ip_address=ip_address, is_active=True).exists()


def is_rate_limited(ip_address):
    """Check if an IP is currently rate limited. Whitelisted IPs are never rate limited."""
    if is_ip_whitelisted(ip_address):
        return False, None
    lockout_key = get_lockout_key(ip_address)
    lockout_until = cache.get(lockout_key)
    if lockout_until:
        return True, lockout_until
    return False, None


def record_failed_attempt(ip_address):
    """
    Record a failed login attempt and check if lockout should be triggered.
    Returns (is_locked_out, attempts_remaining, lockout_until)
    Whitelisted IPs never get locked out.
    """
    if is_ip_whitelisted(ip_address):
        return False, MAX_LOGIN_ATTEMPTS, None

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
        # Persist lockout to DB for admin visibility
        try:
            LoginLockout.objects.create(
                ip_address=ip_address,
                source='ip',
                expires_at=lockout_until,
            )
        except Exception:
            pass
        return True, 0, lockout_until

    return False, MAX_LOGIN_ATTEMPTS - attempts, None


def clear_failed_attempts(ip_address):
    """Clear failed attempts after successful login."""
    cache.delete(get_rate_limit_key(ip_address))
    cache.delete(get_lockout_key(ip_address))


# ── Per-account lockout ───────────────────────────────────────────────────────

# Number of distinct IPs within a window that triggers a distributed-DoS alert
ACCOUNT_LOCKOUT_DISTINCT_IP_THRESHOLD = 3

def get_account_attempts_key(username):
    return f"account_login_attempts_{username}"

def get_account_lockout_key(username):
    return f"account_login_lockout_{username}"

def get_account_ip_set_key(username):
    return f"account_login_ips_{username}"

def is_account_locked(username):
    lockout_until = cache.get(get_account_lockout_key(username))
    if lockout_until:
        return True, lockout_until
    return False, None

def record_account_failed_attempt(username, ip_address=None):
    """
    Increment the per-account failed attempt counter.
    Returns (is_locked, attempts_remaining, lockout_until, distinct_ip_count).
    """
    attempts_key = get_account_attempts_key(username)
    lockout_key = get_account_lockout_key(username)
    ip_set_key = get_account_ip_set_key(username)

    attempts = cache.get(attempts_key, 0) + 1
    cache.set(attempts_key, attempts, ATTEMPT_WINDOW)

    # Track distinct IPs that have contributed to this account's failures
    ip_set = cache.get(ip_set_key) or set()
    if ip_address:
        ip_set.add(ip_address)
        cache.set(ip_set_key, ip_set, ATTEMPT_WINDOW)
    distinct_ips = len(ip_set)

    if attempts >= MAX_ACCOUNT_ATTEMPTS:
        lockout_until = timezone.now() + timedelta(seconds=ACCOUNT_LOCKOUT_DURATION)
        cache.set(lockout_key, lockout_until, ACCOUNT_LOCKOUT_DURATION)
        cache.delete(attempts_key)
        cache.delete(ip_set_key)
        return True, 0, lockout_until, distinct_ips

    return False, MAX_ACCOUNT_ATTEMPTS - attempts, None, distinct_ips

def clear_account_failed_attempts(username):
    cache.delete(get_account_attempts_key(username))
    cache.delete(get_account_lockout_key(username))
    cache.delete(get_account_ip_set_key(username))


def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.GET.get('quarantined'):
        messages.error(
            request,
            "Your session was ended because your account has been flagged for suspicious activity. "
            "Please contact an administrator."
        )

    if request.GET.get('rl'):
        minutes = request.GET.get('rl', '30')
        messages.error(
            request,
            f"Too many failed login attempts. Please try again in {minutes} minutes."
        )

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')[:200]

        # Check IP rate limiting first
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

        # Check per-account lockout (catches distributed attacks on one username)
        if username:
            acct_locked, acct_lockout_until = is_account_locked(username)
            if acct_locked:
                remaining = max(1, (acct_lockout_until - timezone.now()).seconds // 60 + 1)
                messages.error(
                    request,
                    f"This account has been temporarily locked due to too many failed attempts. "
                    f"Please try again in {remaining} minute{'s' if remaining != 1 else ''}."
                )
                logging.getLogger('admin_actions').warning(
                    f"ACCOUNT LOCKED: login attempt for '{username}' from IP {ip_address} while account locked out."
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

        # Support login with email address — look up the username first
        if '@' in username:
            try:
                User = get_user_model()
                lookup = User.objects.get(email__iexact=username)
                username = lookup.username
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                pass  # Fall through to authenticate, which will fail and hit normal error path

        # Use Django's built-in authenticate method for secure password checking
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Check if account is quarantined — credentials are valid so don't
            # count this as a failed login attempt (that would punish the IP).
            if hasattr(user, 'is_quarantined') and user.is_quarantined:
                messages.error(
                    request,
                    "This account has been temporarily locked due to suspicious activity. "
                    "Please contact an administrator."
                )
                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"LOGIN BLOCKED: Quarantined account '{username}' attempted login from IP {ip_address}"
                )
                return redirect('login')

            if user.is_active:
                # Run shared post-auth pipeline: geo, session flags, LoginHistory, LoginAlert,
                # logging, watch-flag alert. Mirrors the passkey login path in webauthn.py.
                error_response, _ = run_post_auth_pipeline(
                    request, user, ip_address, user_agent, method='password'
                )
                if error_response:
                    return error_response

                login(request, user)

                # Clear any failed attempt counters on successful login
                clear_failed_attempts(ip_address)
                clear_account_failed_attempts(username)

                # Create session record for session management
                UserSession.create_or_update_session(user, request)

                messages.success(request, f"Welcome, {user.get_display_name() if hasattr(user, 'get_display_name') else user.name}!")

                # Warn user if their email address has been flagged as undeliverable
                if getattr(user, 'email_flagged', False):
                    messages.warning(
                        request,
                        f'Your email address ({user.email or "none set"}) appears to be invalid or undeliverable. '
                        f'Please update it in your profile so you continue receiving notifications.'
                    )

                # New users get the onboarding wizard first
                if not user.onboarding_complete:
                    request.session['in_onboarding'] = True
                    return redirect('onboarding')

                next_url = request.GET.get('next', '')
                if next_url and url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
                ):
                    return redirect(next_url)

                # Respect user's preferred landing page
                landing = getattr(getattr(user, 'preferences', None), 'landing_page', 'home')
                landing_map = {
                    'announcements': 'announcements',
                    'calendar': 'calendar',
                    'vote': 'vote',
                }
                return redirect(landing_map.get(landing, 'home'))
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
            acct_now_locked, _, _, distinct_ips = record_account_failed_attempt(username, ip_address)

            if acct_now_locked:
                # Account just locked — notify the user if they have an email
                try:
                    User = get_user_model()
                    target = User.objects.filter(username=username).first()
                    if target and target.email:
                        from src.security_notifications import notify_user_security_event
                        notify_user_security_event(
                            target,
                            'Account temporarily locked',
                            f'Your account was temporarily locked for {ACCOUNT_LOCKOUT_DURATION // 60} minutes '
                            f'after {MAX_ACCOUNT_ATTEMPTS} failed login attempts from multiple locations.',
                            ip_address,
                        )
                except Exception:
                    pass
                log_msg = (
                    f"ACCOUNT LOCKOUT: '{username}' locked after {MAX_ACCOUNT_ATTEMPTS} failed attempts "
                    f"(latest IP: {ip_address}, distinct IPs this window: {distinct_ips})"
                )
                if distinct_ips >= ACCOUNT_LOCKOUT_DISTINCT_IP_THRESHOLD:
                    log_msg = f"[DISTRIBUTED LOCKOUT SUSPECTED] {log_msg}"
                logging.getLogger('admin_actions').warning(log_msg)

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

            # --- Watch flag alert (failed logins) ---
            # Trigger when ≥2 failed attempts and the username belongs to a watched user.
            attempts_so_far = MAX_LOGIN_ATTEMPTS - remaining
            if attempts_so_far >= 2:
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    target_user = User.objects.filter(username=username).first()
                    if target_user:
                        watch_flag = getattr(target_user, 'watch_flag', None)
                        if watch_flag and watch_flag.is_active:
                            is_foreign_fail, geo_fail = is_foreign_ip(ip_address)
                            send_watch_flag_alert(
                                watched_user=target_user,
                                event_type='failed',
                                ip_address=ip_address,
                                geo=geo_fail,
                                user_agent=user_agent,
                                is_whitelisted=is_ip_whitelisted(ip_address),
                                is_blacklisted=IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists(),
                                is_rate_limited=is_rate_limited(ip_address)[0],
                                risk_level='high',
                                risk_factors=['repeated_failed_logins'],
                                is_foreign=is_foreign_fail,
                                watch_reason=watch_flag.reason,
                                failed_attempts=attempts_so_far,
                                login_history=None,
                            )
                except Exception as _wf_err:
                    logging.getLogger('admin_actions').error(f"Watch flag alert error (failed): {_wf_err}")

            return redirect('login')

    return render(request, 'registration/login.html')