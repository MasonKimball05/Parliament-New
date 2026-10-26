"""
The password sign-in checks, shared by the web login page and the mobile app.

``login_view`` (``src/view/login_view.py``) and ``POST /api/mobile/v1/auth/login/``
(``src/mobile/views_auth.py``) both call ``attempt_password_login``. It does
every check up to, but NOT including, Django's ``login()``:

    per-IP lockout → per-account lockout → IP blacklist → missing fields →
    email-or-username lookup → authenticate() → quarantine → is_active →
    run_post_auth_pipeline → clear the failure counters

and returns a ``PasswordLoginResult`` saying what happened. The web view turns
that into flash messages and redirects and calls ``login()`` itself; the mobile
view turns it into JSON and mints a device token, never a session. Keeping the
two halves apart is the point: a check added here protects both doors, and the
app path can't accidentally create a session.

The lockout counters and their cache keys are unchanged from when they lived in
``login_view.py`` (which re-exports them), so web and app failures count
against the SAME budget: five wrong passwords in the app lock that IP out of the
web page too, and vice versa. ``clear_lockouts_for`` in
``src/utils/security_utils.py`` knows these keys; change them together.

⚠️ One deliberate tightening over the old inline code: the account lockout is
checked against the username an email address RESOLVES to, as well as the text
typed. Failures were always recorded under the resolved username, but the lock
was only checked under the typed text — so once an account was locked, typing
its email address instead of its username walked straight past the lock.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.utils import timezone

from src.geo_utils import is_foreign_ip
from src.models import IPBlacklist, IPWhitelist, LoginLockout
from src.security_notifications import send_watch_flag_alert
from src.utils.security_utils import run_post_auth_pipeline

# Rate limiting settings
MAX_LOGIN_ATTEMPTS = 5       # Per-IP lockout threshold
LOCKOUT_DURATION = 15 * 60  # Lockout duration in seconds (15 minutes)
ATTEMPT_WINDOW = 15 * 60    # Window to count attempts (15 minutes)

# Per-account lockout (catches distributed/IP-rotating attacks on one username)
MAX_ACCOUNT_ATTEMPTS = 10
ACCOUNT_LOCKOUT_DURATION = 15 * 60

# Number of distinct IPs within a window that triggers a distributed-DoS alert
ACCOUNT_LOCKOUT_DISTINCT_IP_THRESHOLD = 3


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

    attempts = cache.get(attempts_key, 0)
    attempts += 1
    cache.set(attempts_key, attempts, ATTEMPT_WINDOW)

    if attempts >= MAX_LOGIN_ATTEMPTS:
        lockout_until = timezone.now() + timedelta(seconds=LOCKOUT_DURATION)
        cache.set(lockout_key, lockout_until, LOCKOUT_DURATION)
        cache.delete(attempts_key)
        # Persist lockout to DB for admin visibility
        try:
            # v3.21.7 — `record_failed_attempt` is called with whatever the
            # caller resolved, which may be the missing-IP sentinel or None.
            # The column is `inet` and now nullable; see its model note.
            from src.utils.security_utils import ip_or_none
            LoginLockout.objects.create(
                ip_address=ip_or_none(ip_address),
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


# ── The pipeline ─────────────────────────────────────────────────────────────

#: Outcomes. Callers switch on these; the web view maps each to the flash
#: message it always showed, the app maps each to a JSON status.
OK = 'ok'
IP_LOCKED = 'ip_locked'
ACCOUNT_LOCKED = 'account_locked'
BLACKLISTED = 'blacklisted'
MISSING_CREDENTIALS = 'missing_credentials'
INVALID = 'invalid'
DISABLED = 'disabled'
QUARANTINED = 'quarantined'
BLOCKED = 'blocked'   # run_post_auth_pipeline refused (see error_response)


@dataclass
class PasswordLoginResult:
    outcome: str
    user: Any = None
    #: The username the identifier resolved to (email → username), as used
    #: for the per-account counters.
    username: str = ''
    #: When the IP / account lock ends (IP_LOCKED, ACCOUNT_LOCKED, or an
    #: INVALID/DISABLED attempt that just triggered the IP lock).
    lockout_until: Optional[Any] = None
    #: Attempts left before the IP lock (INVALID / DISABLED).
    remaining: Optional[int] = None
    #: True when THIS failure triggered the IP lockout.
    now_locked: bool = False
    #: run_post_auth_pipeline's response (BLOCKED only).
    error_response: Any = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.outcome == OK


def attempt_password_login(request, identifier, password, *, ip_address, user_agent,
                           method='password', use_session=True):
    """
    Verify a password sign-in and run every pre-``login()`` check.

    Does NOT call ``login()``. On ``OK`` the failure counters for this IP and
    account are already cleared and ``run_post_auth_pipeline`` has run (with
    ``use_session`` passed through: the app path passes False so nothing is
    written to a session). The caller then either calls ``login()`` (web) or
    issues a device / MFA ticket (app).
    """
    security_logger = logging.getLogger('admin_actions')
    username = identifier

    # Check IP rate limiting first
    is_locked, lockout_until = is_rate_limited(ip_address)
    if is_locked:
        remaining = (lockout_until - timezone.now()).seconds // 60 + 1
        security_logger.warning(
            f"RATE LIMITED: IP {ip_address} attempted login while locked out. "
            f"Lockout expires in {remaining} minutes."
        )
        return PasswordLoginResult(IP_LOCKED, username=username or '', lockout_until=lockout_until)

    # Check per-account lockout (catches distributed attacks on one username)
    if username:
        acct_locked, acct_lockout_until = is_account_locked(username)
        if acct_locked:
            security_logger.warning(
                f"ACCOUNT LOCKED: login attempt for '{username}' from IP {ip_address} while account locked out."
            )
            return PasswordLoginResult(ACCOUNT_LOCKED, username=username, lockout_until=acct_lockout_until)

    # Check if IP is blacklisted
    blacklist_entry = IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).first()
    if blacklist_entry:
        if blacklist_entry.expires_at and blacklist_entry.expires_at < timezone.now():
            blacklist_entry.is_active = False
            blacklist_entry.save()
        else:
            blacklist_entry.block_count += 1
            blacklist_entry.last_blocked = timezone.now()
            blacklist_entry.save()
            security_logger.warning(
                f"BLOCKED LOGIN: Blacklisted IP {ip_address} attempted login as '{username}'. "
                f"Reason: {blacklist_entry.reason}"
            )
            return PasswordLoginResult(BLACKLISTED, username=username or '')

    if not username or not password:
        security_logger.warning(f"Login attempt with missing credentials from IP {ip_address}")
        return PasswordLoginResult(MISSING_CREDENTIALS, username=username or '')

    # Support login with email address — look up the username first
    User = get_user_model()
    if '@' in username:
        try:
            lookup = User.objects.get(email__iexact=username)
            username = lookup.username
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            pass  # Fall through to authenticate, which will fail and hit normal error path
        if username != identifier:
            # See the module docstring: the lock is recorded under the resolved
            # username, so it must be checked there too.
            acct_locked, acct_lockout_until = is_account_locked(username)
            if acct_locked:
                security_logger.warning(
                    f"ACCOUNT LOCKED: login attempt for '{username}' (by email) from IP "
                    f"{ip_address} while account locked out."
                )
                return PasswordLoginResult(ACCOUNT_LOCKED, username=username,
                                           lockout_until=acct_lockout_until)

    user = authenticate(request, username=username, password=password)

    if user is not None:
        # Credentials are valid so don't count this as a failed attempt
        # (that would punish the IP).
        if getattr(user, 'is_quarantined', False):
            security_logger.warning(
                f"LOGIN BLOCKED: Quarantined account '{username}' attempted login from IP {ip_address}"
            )
            return PasswordLoginResult(QUARANTINED, user=user, username=username)

        if not user.is_active:
            # Disabled account also counts as failed attempt
            now_locked, remaining, lockout_until = record_failed_attempt(ip_address)
            security_logger.warning(
                f"LOGIN FAILED: Attempt to access disabled account '{username}' from IP {ip_address}"
            )
            return PasswordLoginResult(DISABLED, user=user, username=username, remaining=remaining,
                                       now_locked=now_locked, lockout_until=lockout_until)

        # Shared post-auth gate: blacklist, geo, and the context the
        # user_logged_in signal handler uses for LoginHistory/LoginAlert/
        # watch-flag. Mirrors the passkey login path in webauthn.py.
        error_response, _ = run_post_auth_pipeline(
            request, user, ip_address, user_agent, method=method, use_session=use_session,
        )
        if error_response:
            return PasswordLoginResult(BLOCKED, user=user, username=username,
                                       error_response=error_response)

        clear_failed_attempts(ip_address)
        clear_account_failed_attempts(username)
        return PasswordLoginResult(OK, user=user, username=username)

    # ── Wrong credentials ──
    now_locked, remaining, lockout_until = record_failed_attempt(ip_address)
    acct_now_locked, _, _, distinct_ips = record_account_failed_attempt(username, ip_address)

    if acct_now_locked:
        # Account just locked — notify the user if they have an email
        try:
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
        security_logger.warning(log_msg)

    if now_locked:
        security_logger.warning(
            f"RATE LIMIT TRIGGERED: IP {ip_address} locked out after {MAX_LOGIN_ATTEMPTS} failed attempts. "
            f"Username attempted: '{username}'"
        )

    security_logger.warning(
        f"LOGIN FAILED: Invalid credentials for username '{username}' from IP {ip_address} "
        f"(Attempts remaining: {remaining})"
    )

    # --- Watch flag alert (failed logins) ---
    # Trigger when ≥2 failed attempts and the username belongs to a watched user.
    attempts_so_far = MAX_LOGIN_ATTEMPTS - remaining
    if attempts_so_far >= 2:
        try:
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
            security_logger.error(f"Watch flag alert error (failed): {_wf_err}")

    return PasswordLoginResult(INVALID, username=username, remaining=remaining,
                               now_locked=now_locked, lockout_until=lockout_until,
                               extra={'account_now_locked': acct_now_locked})
