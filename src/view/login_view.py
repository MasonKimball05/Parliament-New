from django.contrib.messages import get_messages
from django.utils.http import url_has_allowed_host_and_scheme
from ..models import UserSession
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.utils import timezone
from src.utils.security_utils import get_client_ip
from src import login_pipeline as lp

# The checks themselves live in src/login_pipeline.py (shared with the mobile
# app's sign-in). Re-exported here because callers and tests import them from
# this module; see that module's docstring.
from src.login_pipeline import (  # noqa: F401  (re-exports)
    MAX_LOGIN_ATTEMPTS, LOCKOUT_DURATION, ATTEMPT_WINDOW,
    MAX_ACCOUNT_ATTEMPTS, ACCOUNT_LOCKOUT_DURATION, ACCOUNT_LOCKOUT_DISTINCT_IP_THRESHOLD,
    get_rate_limit_key, get_lockout_key, is_ip_whitelisted, is_rate_limited,
    record_failed_attempt, clear_failed_attempts,
    get_account_attempts_key, get_account_lockout_key, get_account_ip_set_key,
    is_account_locked, record_account_failed_attempt, clear_account_failed_attempts,
)


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

        result = lp.attempt_password_login(
            request, username, password,
            ip_address=ip_address, user_agent=user_agent, method='password',
        )

        if result.outcome == lp.IP_LOCKED:
            remaining = (result.lockout_until - timezone.now()).seconds // 60 + 1
            messages.error(
                request,
                f"Too many failed login attempts. Please try again in {remaining} minute{'s' if remaining != 1 else ''}."
            )
            return redirect('login')

        if result.outcome == lp.ACCOUNT_LOCKED:
            remaining = max(1, (result.lockout_until - timezone.now()).seconds // 60 + 1)
            messages.error(
                request,
                f"This account has been temporarily locked due to too many failed attempts. "
                f"Please try again in {remaining} minute{'s' if remaining != 1 else ''}."
            )
            return redirect('login')

        if result.outcome == lp.BLACKLISTED:
            messages.error(
                request,
                "Access denied. Your IP address has been blocked. Please contact an administrator if you believe this is an error."
            )
            return redirect('login')

        if result.outcome == lp.MISSING_CREDENTIALS:
            messages.error(request, "Both username and password are required.")
            return redirect('login')

        if result.outcome == lp.QUARANTINED:
            messages.error(
                request,
                "This account has been temporarily locked due to suspicious activity. "
                "Please contact an administrator."
            )
            return redirect('login')

        if result.outcome == lp.DISABLED:
            messages.error(request, "This account has been disabled.")
            if result.now_locked:
                messages.warning(
                    request,
                    f"Too many failed attempts. You are now locked out for {LOCKOUT_DURATION // 60} minutes."
                )
            return redirect('login')

        if result.outcome == lp.BLOCKED:
            return result.error_response

        if result.outcome == lp.INVALID:
            if result.now_locked:
                messages.error(
                    request,
                    f"Too many failed login attempts. You are now locked out for {LOCKOUT_DURATION // 60} minutes."
                )
            else:
                remaining = result.remaining
                messages.error(request, "Invalid username or password.")
                if remaining <= 2:
                    messages.warning(request, f"Warning: {remaining} attempt{'s' if remaining != 1 else ''} remaining before lockout.")
            return redirect('login')

        # result.outcome == lp.OK — credentials and every check passed; the
        # counters are cleared. Only the web path creates a session.
        user = result.user
        login(request, user)

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

    return render(request, 'registration/login.html')