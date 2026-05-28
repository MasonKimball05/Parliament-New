from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.utils.http import url_has_allowed_host_and_scheme
from ..models import IPBlacklist, IPWhitelist, UserSession, LoginHistory, LoginAlert, LoginLockout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from src.geo_utils import is_foreign_ip
from src.utils.security_utils import get_client_ip
from src.security_notifications import send_watch_flag_alert
import logging


# Rate limiting settings
MAX_LOGIN_ATTEMPTS = 5  # Maximum failed attempts before lockout
LOCKOUT_DURATION = 15 * 60  # Lockout duration in seconds (15 minutes)
ATTEMPT_WINDOW = 15 * 60  # Window to count attempts in seconds (15 minutes)


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


def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.GET.get('quarantined'):
        messages.error(
            request,
            "Your session was ended because your account has been flagged for suspicious activity. "
            "Please contact an administrator."
        )

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
                login(request, user)

                # Clear any failed attempt counters on successful login
                clear_failed_attempts(ip_address)

                # Create session record for session management
                UserSession.create_or_update_session(user, request)

                # --- Geo check ---
                is_foreign, geo = is_foreign_ip(ip_address)
                risk_factors = []
                if is_foreign:
                    risk_factors.append('non_us_location')

                # Store geo + suspicion flag in session for middleware to read
                request.session['login_geo'] = geo
                request.session['login_geo_suspicious'] = is_foreign
                if geo:
                    request.session['login_geo_country'] = geo.get('country', '')
                    request.session['login_geo_city'] = geo.get('city', '')

                # Record LoginHistory
                try:
                    login_record = LoginHistory.objects.create(
                        user=user,
                        status='success',
                        ip_address=ip_address,
                        country=geo.get('country', '') if geo else '',
                        city=geo.get('city', '') if geo else '',
                        region=geo.get('region', '') if geo else '',
                        latitude=geo.get('lat') if geo else None,
                        longitude=geo.get('lon') if geo else None,
                        user_agent=user_agent,
                        is_suspicious=is_foreign,
                        risk_level='medium' if is_foreign else 'low',
                        risk_factors=risk_factors,
                        alert_created=is_foreign,
                    )
                except Exception as e:
                    login_record = None
                    logging.getLogger('admin_actions').warning(f"Failed to create LoginHistory: {e}")

                # Create LoginAlert for non-US logins
                if is_foreign and geo:
                    try:
                        location_str = ', '.join(filter(None, [geo.get('city'), geo.get('region'), geo.get('country')]))
                        LoginAlert.objects.create(
                            user=user,
                            login_history=login_record,
                            alert_type='new_location',
                            severity='medium',
                            status='new',
                            title=f'Non-US login: {user.name} from {geo.get("country", "Unknown")}',
                            description=(
                                f'{user.name} logged in from outside the United States.\n\n'
                                f'Location: {location_str}\n'
                                f'IP: {ip_address}\n'
                                f'ISP: {geo.get("isp", "Unknown")}\n'
                                f'Coordinates: {geo.get("lat")}, {geo.get("lon")}\n\n'
                                f'The user has been flagged for this session. Sensitive data exports '
                                f'are restricted until they log in from a US IP address.'
                            ),
                        )
                    except Exception as e:
                        logging.getLogger('admin_actions').warning(f"Failed to create LoginAlert: {e}")

                # Log successful login with IP and user agent
                logger = logging.getLogger('function_calls')
                logger.info(
                    f"Successful login: {user.name} ({user.member_type}) (user_id={user.user_id}) "
                    f"from IP {ip_address}"
                    + (f" [{geo.get('city')}, {geo.get('country')}]" if geo else "")
                )

                # Also log to admin_actions for security audit
                security_logger = logging.getLogger('admin_actions')
                if is_foreign:
                    security_logger.warning(
                        f"LOGIN SUCCESS (NON-US): User '{username}' (ID: {user.user_id}) from IP {ip_address} "
                        f"- Location: {geo.get('city', '?')}, {geo.get('country', '?')} "
                        f"(ISP: {geo.get('isp', '?')}). Session flagged as suspicious."
                    )
                else:
                    security_logger.info(
                        f"LOGIN SUCCESS: User '{username}' (ID: {user.user_id}) from IP {ip_address}"
                    )

                # --- Watch flag alert ---
                try:
                    watch_flag = getattr(user, 'watch_flag', None)
                    if watch_flag and watch_flag.is_active:
                        send_watch_flag_alert(
                            watched_user=user,
                            event_type='success',
                            ip_address=ip_address,
                            geo=geo,
                            user_agent=user_agent,
                            is_whitelisted=is_ip_whitelisted(ip_address),
                            is_blacklisted=IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists(),
                            is_rate_limited=is_rate_limited(ip_address)[0],
                            risk_level='medium' if is_foreign else 'low',
                            risk_factors=risk_factors,
                            is_foreign=is_foreign,
                            watch_reason=watch_flag.reason,
                            login_history=login_record,
                        )
                except Exception as _wf_err:
                    logging.getLogger('admin_actions').error(f"Watch flag alert error (success): {_wf_err}")

                messages.success(request, f"Welcome, {user.get_display_name() if hasattr(user, 'get_display_name') else user.name}!")

                # Warn user if their email address has been flagged as undeliverable
                if getattr(user, 'email_flagged', False):
                    messages.warning(
                        request,
                        f'Your email address ({user.email or "none set"}) appears to be invalid or undeliverable. '
                        f'Please update it in your profile so you continue receiving notifications.'
                    )

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