"""
Admin v2 - Advanced administrative interface
Requires dual authentication: user password + secret key
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.db import transaction
from datetime import datetime, timedelta
from src.models_feature_flags import FeatureFlag, PageToggle, SiteSetting
from src.models import (
    ParliamentUser, Legislation, Event, Committee,
    Announcement, ActivityLog, LoginHistory, LoginAlert,
    IPWhitelist, IPBlacklist, AnnouncementEmailLog, AnnouncementEmailRecipient,
    QuarantinedAccount, HoneypotAccess, SecurityNotificationLog, UserWatchFlag,
    PushSubscription, PageVisit,
    EventReminderLog, EventReminderRecipient,
    log_admin_action,
    APIToken, APIAccessLog,
    WebAuthnCredential,
)
import json
import os
import secrets
import string
import logging

import webauthn
from webauthn.helpers.structs import UserVerificationRequirement, PublicKeyCredentialDescriptor
from webauthn import base64url_to_bytes, options_to_json
from src.view.webauthn import _rp_config as _webauthn_rp_config

logger = logging.getLogger('function_calls')
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from src.logging_utils import get_client_ip
from src.middleware.performance import get_performance_summary, get_slow_requests
from src.notifications import send_announcement_notification
from src.notification_service import notify_all_active_members
from src.models.users import member_defer


_raw_allowed_ids = os.environ.get('ADMIN_V2_USER_IDS', os.environ.get('ADMIN_V2_USER_ID', ''))
ALLOWED_USER_IDS = {uid.strip() for uid in _raw_allowed_ids.split(',') if uid.strip()}

ADMIN_V2_MAX_ATTEMPTS = 5
ADMIN_V2_LOCKOUT_SECONDS = 15 * 60  # 15 minutes


def generate_random_password(length=16):
    """Generate a secure random password"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for i in range(length))


def admin_v2_login(request):
    """
    Login page for Admin v2 - requires user password + secret key
    """
    # Check if user is authenticated
    if not request.user.is_authenticated:
        messages.warning(request, 'Please login first to access Admin v2')
        return redirect('login')

    # Check if user is authorized
    if not hasattr(request.user, 'user_id') or request.user.user_id not in ALLOWED_USER_IDS:
        messages.error(request, 'Unauthorized access')
        return redirect('home')

    # Check if already authenticated with valid session (within 7 days)
    if request.session.get('admin_v2_authenticated'):
        auth_time_str = request.session.get('admin_v2_auth_time')
        if auth_time_str:
            try:
                auth_time = datetime.fromisoformat(auth_time_str)
                if auth_time.tzinfo is None:
                    auth_time = timezone.make_aware(auth_time)
                # If within 7 days, redirect to dashboard
                if timezone.now() - auth_time <= timedelta(days=7):
                    return redirect('admin_v2_dashboard')
            except (ValueError, TypeError):
                pass  # Invalid time, continue to login form

    if request.method == 'POST':
        _rate_key = f'admin_v2_attempts_{request.user.pk}'
        _attempts = cache.get(_rate_key, 0)
        if _attempts >= ADMIN_V2_MAX_ATTEMPTS:
            messages.error(request, 'Too many failed attempts. Try again in 15 minutes.')
            return render(request, 'admin_v2/login.html')

        user_password = request.POST.get('user_password', '')
        secret_key = request.POST.get('secret_key', '')

        # Verify user password
        if not request.user.check_password(user_password):
            cache.set(_rate_key, _attempts + 1, ADMIN_V2_LOCKOUT_SECONDS)
            messages.error(request, 'Invalid user password')
            return render(request, 'admin_v2/login.html')

        # Verify secret key from environment
        env_secret = os.environ.get('ADMIN_V2_SECRET_KEY', '')
        if not env_secret:
            messages.error(request, 'Admin v2 secret key not configured. Contact system administrator.')
            return render(request, 'admin_v2/login.html')

        if secret_key != env_secret:
            cache.set(_rate_key, _attempts + 1, ADMIN_V2_LOCKOUT_SECONDS)
            messages.error(request, 'Invalid secret key')
            ActivityLog.log_activity(
                action_type='security_violation',
                user=request.user,
                description=f'Failed Admin v2 secret key attempt by {request.user.get_display_name()}',
                request=request
            )
            return render(request, 'admin_v2/login.html')

        # Both passwords correct - grant access
        cache.delete(_rate_key)
        request.session['admin_v2_authenticated'] = True
        request.session['admin_v2_auth_time'] = timezone.now().isoformat()
        # Explicitly mark session as modified to ensure it's saved
        request.session.modified = True
        # Set session to expire in 7 days (in seconds)
        request.session.set_expiry(7 * 24 * 60 * 60)

        ActivityLog.log_activity(
            action_type='admin_v2_access',
            user=request.user,
            description=f'{request.user.get_display_name()} successfully accessed Admin v2',
            request=request
        )

        messages.success(request, 'Admin v2 access granted')
        return redirect('admin_v2_dashboard')

    return render(request, 'admin_v2/login.html', {
        'has_passkeys': (
            request.user.is_authenticated
            and WebAuthnCredential.objects.filter(user=request.user).exists()
        ),
    })


_SESSION_ADMIN_V2_PASSKEY_CHALLENGE = 'admin_v2_passkey_challenge'
_security_logger = logging.getLogger('security')


@require_POST
def admin_v2_passkey_auth_begin(request):
    """
    Generate WebAuthn assertion options for admin-v2 second-factor auth.
    User must already be logged in and in ALLOWED_USER_IDS.
    Scopes allow_credentials to this user's registered keys so the authenticator
    can prompt immediately without needing a discoverable/resident key.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in.'}, status=403)
    if not hasattr(request.user, 'user_id') or request.user.user_id not in ALLOWED_USER_IDS:
        return JsonResponse({'error': 'Unauthorized.'}, status=403)

    _rate_key = f'admin_v2_attempts_{request.user.pk}'
    if cache.get(_rate_key, 0) >= ADMIN_V2_MAX_ATTEMPTS:
        return JsonResponse({'error': 'Too many failed attempts. Try again in 15 minutes.'}, status=429)

    rp_id, _ = _webauthn_rp_config(request)

    user_creds = list(WebAuthnCredential.objects.filter(user=request.user))
    if not user_creds:
        return JsonResponse({'error': 'No passkeys registered for this account.'}, status=400)

    allowed = [
        PublicKeyCredentialDescriptor(id=bytes(c.credential_id))
        for c in user_creds
    ]

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=allowed,
    )

    request.session[_SESSION_ADMIN_V2_PASSKEY_CHALLENGE] = list(options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@require_POST
def admin_v2_passkey_auth_complete(request):
    """
    Verify a WebAuthn assertion and grant admin-v2 access.
    The credential must belong to the already-logged-in user (ownership enforced).
    On success, sets the same admin_v2_authenticated session flag as password auth.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in.'}, status=403)
    if not hasattr(request.user, 'user_id') or request.user.user_id not in ALLOWED_USER_IDS:
        return JsonResponse({'error': 'Unauthorized.'}, status=403)

    rp_id, origin = _webauthn_rp_config(request)

    challenge_list = request.session.pop(_SESSION_ADMIN_V2_PASSKEY_CHALLENGE, None)
    if not challenge_list:
        return JsonResponse({'error': 'No authentication in progress.'}, status=400)

    _rate_key = f'admin_v2_attempts_{request.user.pk}'
    _attempts = cache.get(_rate_key, 0)
    if _attempts >= ADMIN_V2_MAX_ATTEMPTS:
        return JsonResponse({'error': 'Too many failed attempts. Try again in 15 minutes.'}, status=429)

    try:
        body_json = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid request body.'}, status=400)

    try:
        cred_id_bytes = base64url_to_bytes(body_json['rawId'])
    except Exception:
        return JsonResponse({'error': 'Invalid credential data.'}, status=400)

    # Credential must be registered to the currently authenticated user — no lateral movement possible
    db_cred = WebAuthnCredential.objects.filter(
        credential_id=cred_id_bytes,
        user=request.user,
    ).first()
    if not db_cred:
        cache.set(_rate_key, _attempts + 1, ADMIN_V2_LOCKOUT_SECONDS)
        _security_logger.warning(
            f'Admin-v2 passkey auth: unknown or unauthorized credential from {request.user.username}'
        )
        return JsonResponse({'error': 'Unknown or unauthorized credential.'}, status=400)

    try:
        verification = webauthn.verify_authentication_response(
            credential=request.body.decode(),
            expected_challenge=bytes(challenge_list),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(db_cred.public_key),
            credential_current_sign_count=db_cred.sign_count,
        )
    except Exception as exc:
        cache.set(_rate_key, _attempts + 1, ADMIN_V2_LOCKOUT_SECONDS)
        _security_logger.warning(
            f'Admin-v2 passkey verification failed for {request.user.username}: {exc}'
        )
        ActivityLog.log_activity(
            action_type='security_violation',
            user=request.user,
            description=f'Failed Admin v2 passkey attempt by {request.user.get_display_name()}',
            request=request,
        )
        return JsonResponse({'error': 'Passkey verification failed.'}, status=400)

    # All good — update sign count and grant access
    db_cred.mark_used(verification.new_sign_count)
    cache.delete(_rate_key)

    request.session['admin_v2_authenticated'] = True
    request.session['admin_v2_auth_time'] = timezone.now().isoformat()
    request.session.modified = True
    request.session.set_expiry(7 * 24 * 60 * 60)

    ActivityLog.log_activity(
        action_type='admin_v2_access',
        user=request.user,
        description=f'{request.user.get_display_name()} accessed Admin v2 via passkey "{db_cred.name}"',
        request=request,
    )

    from django.urls import reverse
    return JsonResponse({'ok': True, 'redirect': reverse('admin_v2_dashboard')})


def require_admin_v2_auth(view_func):
    """
    Decorator to require Admin v2 authentication
    Authentication is valid for 7 days before requiring re-authentication
    """
    def wrapper(request, *args, **kwargs):
        # Check if user is authenticated
        if not request.user.is_authenticated:
            messages.warning(request, 'Please login to access Admin v2')
            return redirect('login')

        # Check if user is authorized
        # v3.17.1: both halves of the admin-v2 gate are recorded for dev mode.
        # This is a two-factor gate (env allowlist + a separate authenticated
        # session), and when it redirects you want to know WHICH half refused.
        from src.dev_mode import record_permission

        on_allowlist = (
            hasattr(request.user, 'user_id') and request.user.user_id in ALLOWED_USER_IDS
        )
        record_permission(
            'admin_v2: ADMIN_V2_USER_IDS allowlist',
            'allowed' if on_allowlist else 'DENIED',
            f'user_id={getattr(request.user, "user_id", "?")}',
        )
        if not on_allowlist:
            messages.error(request, 'Unauthorized access')
            return redirect('home')

        # Check if Admin v2 session is active and not expired
        session_ok = bool(request.session.get('admin_v2_authenticated'))
        record_permission(
            'admin_v2: session authenticated',
            'allowed' if session_ok else 'DENIED',
            'separate password/passkey step, 7-day expiry',
        )
        if not session_ok:
            messages.warning(request, 'Please authenticate to access Admin v2')
            return redirect('admin_v2_login')

        # Check if authentication has expired (7 days)
        auth_time_str = request.session.get('admin_v2_auth_time')
        if auth_time_str:
            try:
                auth_time = datetime.fromisoformat(auth_time_str)
                # Make auth_time timezone-aware if it isn't
                if auth_time.tzinfo is None:
                    auth_time = timezone.make_aware(auth_time)

                # Check if more than 7 days have passed
                if timezone.now() - auth_time > timedelta(days=7):
                    # Clear expired authentication
                    request.session['admin_v2_authenticated'] = False
                    request.session['admin_v2_auth_time'] = None
                    messages.warning(request, 'Admin v2 session expired. Please re-authenticate.')
                    return redirect('admin_v2_login')
            except (ValueError, TypeError):
                # Invalid auth time format, require re-authentication
                request.session['admin_v2_authenticated'] = False
                request.session['admin_v2_auth_time'] = None
                messages.warning(request, 'Please authenticate to access Admin v2')
                return redirect('admin_v2_login')
        else:
            # No auth time stored, require re-authentication
            request.session['admin_v2_authenticated'] = False
            messages.warning(request, 'Please authenticate to access Admin v2')
            return redirect('admin_v2_login')

        return view_func(request, *args, **kwargs)
    return wrapper


def _safe_count(fn):
    """Run a DB count query, returning 0 if the table doesn't exist yet (pre-migration)."""
    try:
        return fn()
    except Exception:
        return 0


def _safe_agg(fn, fallback):
    """
    `_safe_count` for an `aggregate()` — returns `fallback` if the table isn't
    there yet.

    v3.17.3: needed because collapsing several `_safe_count` calls on one table
    into a single `aggregate()` also collapses their error handling. Returning
    the dict of zeros keeps the pre-migration behaviour the originals had.
    """
    try:
        return fn()
    except Exception:
        return dict(fallback)



def _seed_site_settings(defaults):
    """
    Create any missing SiteSetting rows in one SELECT + one bulk INSERT.

    v3.17.3: was `get_or_create` per key, in two separate loops on the admin-v2
    dashboard — six uncached SELECTs on every load (plus an INSERT each the
    first time). Same shape as the push-flag seeding fix in the same view, and
    the same reasoning: seeding is a write-path idiom and this runs on a GET.

    `ignore_conflicts` covers the race with a concurrent officer, which is what
    get_or_create's own IntegrityError branch was doing.
    """
    keys = [d['key'] for d in defaults]
    existing = set(
        SiteSetting.objects.filter(key__in=keys).values_list('key', flat=True)
    )
    missing = [
        SiteSetting(
            key=d['key'],
            display_name=d['display_name'],
            description=d['description'],
            category=d['category'],
            setting_type=d['setting_type'],
            value=d['default_value'],
            default_value=d['default_value'],
        )
        for d in defaults if d['key'] not in existing
    ]
    if missing:
        SiteSetting.objects.bulk_create(missing, ignore_conflicts=True)

@require_admin_v2_auth
def admin_v2_dashboard(request):
    """
    Main Admin v2 dashboard showing site statistics and controls
    """
    from src.models import CommitteeDocument, Vote, CommitteeVote
    from django.db import connection

    # Gather comprehensive site statistics
    # User counts are collapsed into a single aggregate query (11 conditional COUNTs → 1 SQL query).
    _user_agg = ParliamentUser.objects.aggregate(
        total=Count('user_id', filter=~Q(member_status='Removed')),
        active=Count('user_id', filter=Q(member_status='Active')),
        inactive=Count('user_id', filter=Q(member_status='Inactive')),
        alumni=Count('user_id', filter=Q(member_status='Alumni')),
        officers=Count('user_id', filter=Q(member_type='Officer')),
        members=Count('user_id', filter=Q(member_type='Member')),
        pledges=Count('user_id', filter=Q(member_type='Pledge')),
        advisors=Count('user_id', filter=Q(member_type='Advisor')),
        admins=Count('user_id', filter=Q(is_admin=True)),
        last_24h=Count('user_id', filter=Q(last_login__gte=timezone.now() - timezone.timedelta(hours=24))),
        never_logged_in=Count('user_id', filter=Q(last_login__isnull=True)),
    )

    # v3.17.3: the rest of the dashboard's numbers, one aggregate per table.
    #
    # The user block above was collapsed in an earlier pass and the pattern was
    # never carried to its eleven neighbours: profiling this page found **46
    # separate COUNT queries across 28 tables** — five on Legislation, five on
    # Event, four each on Committee and APIToken, three each on Announcement,
    # ActivityLog, LoginHistory and LoginAlert, and so on. None of them repeated
    # a *shape*, so the N+1 detector was quiet; it was breadth, not a loop, and
    # breadth is why the page cost ~85 queries.
    #
    # Conditional aggregation evaluates every predicate for a table in a single
    # pass, so each group below is now one round trip. Every value is computed
    # from the same predicate as before — a probe captured all 70 numbers this
    # page publishes and asserted them unchanged.
    _now = timezone.now()
    _24h = _now - timezone.timedelta(hours=24)
    _7d = _now - timezone.timedelta(days=7)
    _month_start = _now.replace(day=1, hour=0, minute=0, second=0)
    _next_month = (_month_start + timezone.timedelta(days=32)).replace(day=1)

    _legislation_agg = Legislation.objects.aggregate(
        total=Count('pk'),
        draft=Count('pk', filter=Q(status='draft')),
        passed=Count('pk', filter=Q(status='passed')),
        removed=Count('pk', filter=Q(status='removed')),
        voting_closed=Count('pk', filter=Q(voting_closed=True)),
    )
    _vote_total = Vote.objects.count()

    _event_agg = Event.objects.aggregate(
        total=Count('pk'),
        upcoming=Count('pk', filter=Q(date_time__gte=_now, is_active=True)),
        past=Count('pk', filter=Q(date_time__lt=_now)),
        archived=Count('pk', filter=Q(archived=True)),
        this_month=Count('pk', filter=Q(date_time__gte=_month_start,
                                        date_time__lt=_next_month)),
    )

    # `with_members` was `annotate(Count('members')).filter(member_count__gt=0)
    # .count()`. As a conditional count that is "has at least one row in the M2M",
    # i.e. members__isnull=False — and `distinct=True` is required because the
    # join multiplies a committee by its member count.
    _committee_agg = Committee.objects.aggregate(
        total=Count('pk', distinct=True),
        active=Count('pk', filter=Q(is_active=True), distinct=True),
        inactive=Count('pk', filter=Q(is_active=False), distinct=True),
        with_members=Count('pk', filter=Q(members__isnull=False), distinct=True),
    )
    _document_agg = CommitteeDocument.objects.aggregate(
        total_documents=Count('pk'),
        published_docs=Count('pk', filter=Q(published_to_chapter=True)),
    )
    _committee_vote_total = CommitteeVote.objects.count()

    _announcement_agg = Announcement.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
        inactive=Count('pk', filter=Q(is_active=False)),
    )

    _activity_agg = ActivityLog.objects.aggregate(
        total_activity_logs=Count('pk'),
        logs_last_24h=Count('pk', filter=Q(timestamp__gte=_24h)),
        logs_last_7d=Count('pk', filter=Q(timestamp__gte=_7d)),
    )

    # `failed_logins_24h` keeps its `hasattr` guard: LoginHistory has no
    # `successful` field, so the original always produced 0 and no query. Left
    # as a literal rather than silently changing what the card shows.
    _login_agg = LoginHistory.objects.aggregate(
        total_logins=Count('pk'),
        logins_24h=Count('pk', filter=Q(timestamp__gte=_24h)),
        logins_7d=Count('pk', filter=Q(timestamp__gte=_7d)),
    )
    _login_agg['failed_logins_24h'] = (
        LoginHistory.objects.filter(timestamp__gte=_24h, successful=False).count()
        if hasattr(LoginHistory, 'successful') else 0
    )

    # `new_login_alerts` and `recent_alerts` are the same number under two names
    # — the template uses both. Now one COUNT feeds both instead of two queries.
    _alert_agg = LoginAlert.objects.aggregate(
        total_alerts=Count('pk'),
        _new=Count('pk', filter=Q(status='new')),
    )
    _alert_agg['new_login_alerts'] = _alert_agg['_new']
    _alert_agg['recent_alerts'] = _alert_agg.pop('_new')

    _quarantine_total = _safe_count(lambda: QuarantinedAccount.objects.filter(
        released_at__isnull=True
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=_now)
    ).count())
    _blocked_ip_total = _safe_count(
        lambda: IPBlacklist.objects.filter(is_active=True).count())
    _honeypot_total = _safe_count(
        lambda: HoneypotAccess.objects.filter(accessed_at__gte=_24h).count())
    _security_notification_agg = _safe_agg(
        lambda: SecurityNotificationLog.objects.aggregate(
            security_notifications_24h=Count('pk', filter=Q(sent_at__gte=_24h)),
            critical_notifications=Count(
                'pk', filter=Q(severity__in=['high', 'critical'], sent_at__gte=_24h)),
        ),
        {'security_notifications_24h': 0, 'critical_notifications': 0},
    )

    _api_token_agg = APIToken.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(status=APIToken.STATUS_ACTIVE)),
        pending=Count('pk', filter=Q(status=APIToken.STATUS_PENDING)),
        revoked=Count('pk', filter=Q(status=APIToken.STATUS_REVOKED)),
    )
    _api_log_agg = APIAccessLog.objects.aggregate(
        requests_24h=Count('pk', filter=Q(timestamp__gte=_24h)),
        requests_7d=Count('pk', filter=Q(timestamp__gte=_7d)),
    )

    stats = {
        'users': _user_agg,
        'legislation': {
            **_legislation_agg,
            'total_votes': _vote_total,
            'recent_votes': 0,  # Vote model doesn't have timestamp field
        },
        'events': _event_agg,
        'committees': {
            **_committee_agg,
            **_document_agg,
            'total_committee_votes': _committee_vote_total,
        },
        'announcements': _announcement_agg,
        'communications': {
            'total_channels': 0,  # Channel model not yet implemented
            **_activity_agg,
        },
        'security': {
            **_login_agg,
            **_alert_agg,
            'quarantined_accounts': _quarantine_total,
            'blocked_ips': _blocked_ip_total,
            'honeypot_24h': _honeypot_total,
            **_security_notification_agg,
        },
        'database': {
            'tables': len(connection.introspection.table_names()),
        },
        'api': {
            **_api_token_agg,
            **_api_log_agg,
        },
    }

    push_flag_defaults = [
        {
            'name': 'push_notifications_enabled',
            'display_name': 'Push Notifications (Master)',
            'description': 'Master switch — disabling this stops all push notifications regardless of per-type settings.',
            'category': 'communications',
            'is_enabled': True,
        },
        {
            'name': 'push_announcements',
            'display_name': 'Push: Announcements',
            'description': 'Send push notifications when a new announcement is posted.',
            'category': 'communications',
            'is_enabled': True,
        },
        {
            'name': 'push_legislation',
            'display_name': 'Push: Legislation / Votes',
            'description': 'Send push notifications for new legislation and vote open/close events.',
            'category': 'communications',
            'is_enabled': True,
        },
        {
            'name': 'push_events',
            'display_name': 'Push: Events',
            'description': 'Send push notifications for event reminders.',
            'category': 'communications',
            'is_enabled': True,
        },
        {
            'name': 'push_slating',
            'display_name': 'Push: Slating',
            'description': 'Send push notifications for slating stage transitions.',
            'category': 'communications',
            'is_enabled': True,
        },
    ]

    # Seed any missing push flags.
    #
    # v3.17.3: was `get_or_create` per flag — five uncached SELECTs (plus an
    # INSERT each on first load) on every load of this dashboard. Worth being
    # precise about why v3.17.1's flag caching did not help: that cached
    # `FeatureFlag.is_feature_enabled`, and this code never calls it. It goes
    # to the manager directly, so it bypassed the cache entirely — which is a
    # reminder that "we cached the flag lookup" is only true of the lookup that
    # was cached.
    #
    # One SELECT for the names that already exist, one bulk INSERT for the rest,
    # and nothing at all in the common case where they are all present.
    # `ignore_conflicts` covers the race with a concurrent officer, which is
    # what get_or_create's own IntegrityError branch was doing.
    _existing_push = set(
        FeatureFlag.objects
        .filter(name__in=[f['name'] for f in push_flag_defaults])
        .values_list('name', flat=True)
    )
    _missing_push = [
        FeatureFlag(
            name=flag_data['name'],
            display_name=flag_data['display_name'],
            description=flag_data['description'],
            category=flag_data['category'],
            is_enabled=flag_data['is_enabled'],
        )
        for flag_data in push_flag_defaults
        if flag_data['name'] not in _existing_push
    ]
    if _missing_push:
        FeatureFlag.objects.bulk_create(_missing_push, ignore_conflicts=True)
        # bulk_create does not send post_save, so the cache invalidation
        # receivers in models_feature_flags.py never fire. Invalidate by hand —
        # these names were just answered from the fail-open default and are
        # cached as such.
        for flag in _missing_push:
            FeatureFlag.invalidate_cache(flag.name)
        cache.delete('context_feature_flags')

    # Get feature flags grouped by category — one query instead of two per category.
    # v3.17.3: materialised once and reused for the push-notification card
    # further down, which was re-querying the same table for a subset.
    _all_flags = list(FeatureFlag.objects.order_by('category', 'name'))
    feature_flags = {}
    for flag in _all_flags:
        label = flag.get_category_display()
        feature_flags.setdefault(label, []).append(flag)

    # Get page toggles
    page_toggles = PageToggle.objects.all().order_by('display_name')

    # Ensure chat settings exist
    # Note: chat_active_poll_interval and chat_inactive_poll_interval were removed in v3.0.0
    # — messages are now pushed via WebSocket (Django Channels), not polled.
    chat_settings_defaults = [
        {
            'key': 'chat_active_users_poll_interval',
            'display_name': 'Active Users Poll Interval',
            'description': 'How often (in milliseconds) to refresh the active users list. Messages are real-time via WebSocket.',
            'category': 'chat',
            'setting_type': 'integer',
            'default_value': '5000',
        },
    ]

    # Get chat settings (excludes legacy polling keys if they still exist in DB)
    chat_settings = SiteSetting.objects.filter(
        category='chat',
        key='chat_active_users_poll_interval',
    )

    # Ensure event reminder settings exist
    event_reminder_defaults = [
        {
            'key': 'event_reminders_enabled',
            'display_name': 'Event Reminder Notifications',
            'description': 'Master switch for event push reminder notifications. When off, no reminders are sent regardless of per-event settings.',
            'category': 'notifications',
            'setting_type': 'boolean',
            'default_value': 'true',
        },
        {
            'key': 'event_reminder_default_hours',
            'display_name': 'Default Reminder Lead Time (hours)',
            'description': 'Default number of hours before an event to send the push reminder. Officers can override this per event.',
            'category': 'notifications',
            'setting_type': 'integer',
            'default_value': '24',
        },
    ]
    # Both groups seeded together: one SELECT + one bulk INSERT for the lot.
    _seed_site_settings(chat_settings_defaults + event_reminder_defaults)
    event_reminder_settings = SiteSetting.objects.filter(
        key__in=['event_reminders_enabled', 'event_reminder_default_hours']
    ).order_by('key')

    # Seed push notification feature flags

    # Push notification stats
    # v3.17.3: filtered in Python from the list fetched above rather than a
    # second SELECT. NOTE the ordering: the query was `order_by('name')` while
    # `_all_flags` is `('category', 'name')`, so re-sort to keep the card's order
    # byte-identical.
    push_flags = sorted(
        (f for f in _all_flags if f.name.startswith('push_')),
        key=lambda f: f.name,
    )
    # v3.17.3: two COUNTs over the same table → one aggregate. `distinct=True`
    # on the subscriber count preserves the `.values('user').distinct()` meaning
    # (people, not devices).
    push_stats = _safe_agg(
        lambda: PushSubscription.objects.aggregate(
            total_subscribers=Count('user', distinct=True),
            total_devices=Count('pk'),
        ),
        {'total_subscribers': 0, 'total_devices': 0},
    )

    # Recent activity logs (last 30)
    recent_logs = ActivityLog.objects.select_related('user').defer(*member_defer('user')).order_by('-timestamp')[:30]

    # Recent logins (last 20)
    recent_logins = LoginHistory.objects.select_related('user').defer(*member_defer('user')).order_by('-timestamp')[:20]

    # Recent users (last 10 created)
    recent_users = ParliamentUser.objects.order_by('-date_joined')[:10] if hasattr(ParliamentUser, 'date_joined') else []

    # System info
    import sys
    import django
    system_info = {
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'django_version': django.get_version(),
        'debug_mode': settings.DEBUG,
        'database_engine': settings.DATABASES['default']['ENGINE'].split('.')[-1],
    }

    # Performance metrics
    performance_summary = get_performance_summary()
    slow_requests = get_slow_requests(threshold_ms=1000, limit=5)

    from src.models import SystemLockdown
    lockdown = SystemLockdown.get_instance()

    # Onboarding completion stats for dashboard widget
    _onboarding_agg = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive']
    ).aggregate(
        total=Count('user_id'),
        completed=Count('user_id', filter=Q(onboarding_complete=True)),
    )
    onboarding_stats = {
        'total': _onboarding_agg['total'] or 0,
        'completed': _onboarding_agg['completed'] or 0,
        'pending': (_onboarding_agg['total'] or 0) - (_onboarding_agg['completed'] or 0),
        'percent': round(
            (_onboarding_agg['completed'] / _onboarding_agg['total'] * 100)
            if _onboarding_agg['total'] else 0
        ),
    }

    # Page visit summary for dashboard card (PageVisit is cumulative — no timestamps)
    from django.db.models import Sum as _Sum
    # v3.17.3: the sum and the distinct-path count are one pass, not two.
    _pv_agg = _safe_agg(
        lambda: PageVisit.objects.aggregate(
            total_hits=_Sum('count'),
            unique_paths=Count('path', distinct=True),
        ),
        {'total_hits': 0, 'unique_paths': 0},
    )
    page_visit_summary = {
        'total_hits': _pv_agg['total_hits'] or 0,
        'unique_paths': _pv_agg['unique_paths'] or 0,
        'top_paths': list(
            PageVisit.objects.values('path')
            .annotate(total=_Sum('count'))
            .order_by('-total')[:5]
        ),
    }

    # Celery health summary for dashboard card
    celery_summary = {'workers_up': False, 'task_count': 0, 'stale_count': 0, 'disabled_count': 0}
    try:
        from django_celery_beat.models import PeriodicTask as _PT, IntervalSchedule as _IV
        from celery import current_app as _celery_app
        ping = _celery_app.control.inspect(timeout=1).ping() or {}
        celery_summary['workers_up'] = bool(ping)
        tasks = list(_PT.objects.select_related('interval', 'crontab').all())
        celery_summary['task_count'] = len(tasks)
        celery_summary['disabled_count'] = sum(1 for t in tasks if not t.enabled)
        _period_secs = {'days': 86400, 'hours': 3600, 'minutes': 60, 'seconds': 1}
        stale = 0
        for t in tasks:
            if t.enabled and t.last_run_at:
                if t.interval:
                    threshold = t.interval.every * _period_secs.get(t.interval.period, 60) * 2
                    if (timezone.now() - t.last_run_at).total_seconds() > threshold:
                        stale += 1
                elif t.crontab and (timezone.now() - t.last_run_at).total_seconds() > 90000:
                    stale += 1
        celery_summary['stale_count'] = stale
    except Exception:
        pass

    context = {
        'stats': stats,
        'feature_flags': feature_flags,
        'page_toggles': page_toggles,
        'chat_settings': chat_settings,
        'event_reminder_settings': event_reminder_settings,
        'push_flags': push_flags,
        'push_stats': push_stats,
        'recent_logs': recent_logs,
        'recent_logins': recent_logins,
        'recent_users': recent_users,
        'system_info': system_info,
        'performance': performance_summary,
        'slow_requests': slow_requests,
        'lockdown_active': lockdown.is_active,
        'page_visit_summary': page_visit_summary,
        'celery_summary': celery_summary,
        'onboarding_stats': onboarding_stats,
    }

    return render(request, 'admin_v2/dashboard.html', context)


@require_admin_v2_auth
def toggle_feature_flag(request, flag_id):
    """
    Toggle a feature flag on/off
    """
    if request.method == 'POST':
        try:
            flag = FeatureFlag.objects.get(id=flag_id)
            flag.is_enabled = not flag.is_enabled
            flag.last_toggled_by = request.user.get_display_name()
            flag.last_toggled_at = timezone.now()
            flag.save(update_fields=['is_enabled', 'last_toggled_by', 'last_toggled_at'])

            status = "enabled" if flag.is_enabled else "disabled"
            messages.success(request, f'Feature "{flag.display_name}" has been {status}')

            ActivityLog.log_activity(
                action_type='feature_flag_toggle',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} feature: {flag.display_name}',
                request=request
            )
        except FeatureFlag.DoesNotExist:
            messages.error(request, 'Feature flag not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def toggle_page(request, toggle_id):
    """
    Toggle a page on/off
    """
    if request.method == 'POST':
        try:
            toggle = PageToggle.objects.get(id=toggle_id)
            toggle.is_enabled = not toggle.is_enabled
            toggle.last_toggled_by = request.user.get_display_name()
            toggle.last_toggled_at = timezone.now()
            toggle.save(update_fields=['is_enabled', 'last_toggled_by', 'last_toggled_at'])

            status = "enabled" if toggle.is_enabled else "disabled"

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'is_enabled': toggle.is_enabled, 'status': status})

            messages.success(request, f'Page "{toggle.display_name}" has been {status}')

            ActivityLog.log_activity(
                action_type='page_toggle',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} page: {toggle.display_name}',
                request=request
            )
        except PageToggle.DoesNotExist:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'error': 'Not found'}, status=404)
            messages.error(request, 'Page toggle not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def update_site_setting(request, setting_id):
    """
    Update a site setting value
    """
    if request.method == 'POST':
        try:
            setting = SiteSetting.objects.get(id=setting_id)
            new_value = request.POST.get('value', '').strip()

            # Validate based on setting type
            if setting.setting_type == 'integer':
                try:
                    int(new_value)
                except ValueError:
                    messages.error(request, f'Invalid value for {setting.display_name}. Must be a number.')
                    return redirect('admin_v2_dashboard')
            elif setting.setting_type == 'boolean':
                new_value = 'true' if new_value.lower() in ('true', '1', 'yes', 'on') else 'false'

            old_value = setting.value
            setting.value = new_value
            setting.last_modified_by = request.user.get_display_name()
            setting.save(update_fields=['value', 'last_modified_by'])

            messages.success(request, f'Setting "{setting.display_name}" updated to {new_value}')

            ActivityLog.log_activity(
                action_type='setting_change',
                user=request.user,
                description=f'{request.user.get_display_name()} changed {setting.display_name} from {old_value} to {new_value}',
                request=request
            )
        except SiteSetting.DoesNotExist:
            messages.error(request, 'Setting not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def push_subscriptions_list(request):
    """
    List all push subscriptions grouped by user, with individual delete.
    """
    subscriptions = PushSubscription.objects.select_related('user').defer(*member_defer('user')).order_by('user__name', '-created_at')
    return render(request, 'admin_v2/push_subscriptions.html', {'subscriptions': subscriptions})


@require_admin_v2_auth
@require_POST
def delete_push_subscription(request, sub_id):
    """
    Delete a single push subscription by ID.
    """
    sub = get_object_or_404(PushSubscription, id=sub_id)
    user_display = str(sub.user)
    sub.delete()
    messages.success(request, f'Deleted push subscription for {user_display}.')

    ActivityLog.log_activity(
        action_type='push_subscription_deleted',
        user=request.user,
        description=f'{request.user.get_display_name()} deleted push subscription {sub_id} for {user_display}',
        request=request
    )

    return redirect('admin_v2_push_subscriptions')


@require_admin_v2_auth
@require_POST
def clear_push_subscriptions(request):
    """
    Delete all push subscriptions (nuclear option — forces all devices to re-subscribe).
    """
    count, _ = PushSubscription.objects.all().delete()
    messages.success(request, f'Cleared {count} push subscription(s). Users will need to re-subscribe.')

    ActivityLog.log_activity(
        action_type='push_subscriptions_cleared',
        user=request.user,
        description=f'{request.user.get_display_name()} cleared all push subscriptions ({count} removed)',
        request=request
    )

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def admin_v2_url_explorer(request):
    """
    Enumerate every registered URL pattern grouped by section prefix.
    Introspects Django's URL resolver so no manual maintenance is needed.
    """
    from django.urls import get_resolver
    import inspect

    def _iter_patterns(resolver, prefix=''):
        """Recursively yield (full_pattern, name, callback) triples."""
        for pattern in resolver.url_patterns:
            full = prefix + str(pattern.pattern)
            if hasattr(pattern, 'url_patterns'):
                yield from _iter_patterns(pattern, full)
            else:
                yield full, getattr(pattern, 'name', None), getattr(pattern, 'callback', None)

    def _auth_level(callback):
        """Guess the auth level from decorator chain / closure."""
        if callback is None:
            return 'unknown'
        # Unwrap functools.wraps chains
        func = callback
        while hasattr(func, '__wrapped__'):
            func = func.__wrapped__
        src = ''
        try:
            src = inspect.getsource(func)
        except Exception:
            pass
        closures = []
        cb = callback
        while hasattr(cb, '__closure__') and cb.__closure__:
            for cell in cb.__closure__:
                try:
                    closures.append(type(cell.cell_contents).__name__)
                except Exception:
                    pass
            cb = getattr(cb, '__wrapped__', None) or (cb.__closure__[0].cell_contents if cb.__closure__ else None)
            if not callable(cb):
                break
        name = getattr(callback, '__name__', '') or ''
        qualname = getattr(callback, '__qualname__', '') or ''

        if 'require_admin_v2_auth' in qualname or 'admin_v2_authenticated' in src:
            return 'admin-v2'
        if 'staff_member_required' in str(getattr(callback, '__closure__', '')) or 'staff_member_required' in qualname:
            return 'staff'
        if 'is_officer' in src or 'officer_required' in src:
            return 'officer'
        if 'login_required' in qualname or 'LoginRequiredMixin' in src:
            return 'login'
        if 'csrf_exempt' in qualname:
            return 'public (csrf_exempt)'
        # Class-based views
        view_class = getattr(callback, 'view_class', None)
        if view_class:
            mro_names = [c.__name__ for c in view_class.__mro__]
            if 'LoginRequiredMixin' in mro_names:
                return 'login'
        return 'public'

    def _section(pattern):
        """Derive a section label from the URL prefix."""
        parts = [p for p in pattern.lstrip('/').split('/') if p and '<' not in p and not p.isdigit()]
        if not parts:
            return 'root'
        return parts[0]

    # Key the cache on urls.py's mtime so it auto-invalidates on deploy
    # (collectstatic / git pull touches urls.py whenever routes change).
    # Falls back to a static key if the mtime can't be read.
    try:
        _urls_mtime = int(os.path.getmtime(
            os.path.join(settings.BASE_DIR, 'src', 'urls.py')
        ))
    except Exception:
        _urls_mtime = 0
    _CACHE_KEY = f'admin_v2_url_explorer_data_{_urls_mtime}'
    _CACHE_TTL = 60 * 60 * 24  # 24 h — effectively infinite; mtime-keyed invalidation handles freshness

    cached = cache.get(_CACHE_KEY)
    if cached:
        sorted_sections, total = cached
    else:
        raw = list(_iter_patterns(get_resolver()))

        # Group by section
        sections = {}
        for pattern, name, callback in raw:
            section = _section(pattern)
            entry = {
                'pattern': '/' + pattern.lstrip('^').rstrip('$'),
                'name': name or '—',
                'auth': _auth_level(callback),
                'doc': (inspect.getdoc(callback) or '').split('\n')[0][:120] if callback else '',
            }
            sections.setdefault(section, []).append(entry)

        # Sort sections and entries within each section
        sorted_sections = {k: sorted(v, key=lambda e: e['pattern']) for k, v in sorted(sections.items())}
        total = sum(len(v) for v in sorted_sections.values())
        cache.set(_CACHE_KEY, (sorted_sections, total), _CACHE_TTL)

    return render(request, 'admin_v2/url_explorer.html', {
        'sections': sorted_sections,
        'total': total,
    })


@require_admin_v2_auth
def admin_v2_logout(request):
    """
    Logout from Admin v2
    """
    request.session.pop('admin_v2_authenticated', None)
    request.session.pop('admin_v2_auth_time', None)

    ActivityLog.log_activity(
        action_type='admin_v2_logout',
        user=request.user,
        description=f'{request.user.get_display_name()} logged out of Admin v2',
        request=request
    )

    messages.success(request, 'Logged out of Admin v2')
    return redirect('home')


# ===== MANAGEMENT VIEWS =====

@require_admin_v2_auth
def manage_legislation(request):
    """
    Manage all legislation with filtering, editing, and deletion
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    search_query = request.GET.get('search', '')

    # Build query
    legislation_list = Legislation.objects.select_related('posted_by').defer(*member_defer('posted_by')).order_by('-created_at')

    if status_filter:
        legislation_list = legislation_list.filter(status=status_filter)

    if search_query:
        # ParliamentUser has no first_name/last_name (AbstractBaseUser) — the
        # old lookups here raised FieldError whenever the search box was used.
        # (v3.15.6, same latent bug as the page-visits drill filter.)
        legislation_list = legislation_list.filter(
            Q(title__icontains=search_query) |
            Q(posted_by__name__icontains=search_query) |
            Q(posted_by__preferred_name__icontains=search_query) |
            Q(posted_by__username__icontains=search_query)
        )

    # Paginate
    paginator = Paginator(legislation_list, 25)
    page_number = request.GET.get('page')
    legislation = paginator.get_page(page_number)

    context = {
        'legislation': legislation,
        'status_filter': status_filter,
        'search_query': search_query,
        'status_choices': ['draft', 'passed', 'removed'],
    }

    return render(request, 'admin_v2/manage_legislation.html', context)


@require_admin_v2_auth
def delete_legislation(request, legislation_id):
    """
    Delete a piece of legislation
    """
    if request.method == 'POST':
        try:
            legislation = Legislation.objects.get(id=legislation_id)
            title = legislation.title
            legislation.delete()

            ActivityLog.log_activity(
                action_type='legislation_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted legislation: {title}',
                request=request
            )

            messages.success(request, f'Legislation "{title}" has been deleted')
        except Legislation.DoesNotExist:
            messages.error(request, 'Legislation not found')

    return redirect('admin_v2_manage_legislation')


@require_admin_v2_auth
def manage_events(request):
    """
    Manage all events with filtering and editing
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    archived_filter = request.GET.get('archived', '')
    active_filter = request.GET.get('active', '')
    search_query = request.GET.get('search', '')

    # Build query
    events_list = Event.objects.order_by('-date_time')

    if archived_filter == 'yes':
        events_list = events_list.filter(archived=True)
    elif archived_filter == 'no':
        events_list = events_list.filter(archived=False)

    if active_filter == 'yes':
        events_list = events_list.filter(is_active=True)
    elif active_filter == 'no':
        events_list = events_list.filter(is_active=False)

    if search_query:
        events_list = events_list.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))

    # Paginate
    paginator = Paginator(events_list, 25)
    page_number = request.GET.get('page')
    events = paginator.get_page(page_number)

    context = {
        'events': events,
        'archived_filter': archived_filter,
        'active_filter': active_filter,
        'search_query': search_query,
    }

    return render(request, 'admin_v2/manage_events.html', context)


@require_admin_v2_auth
def delete_event(request, event_id):
    """
    Delete an event
    """
    if request.method == 'POST':
        try:
            event = Event.objects.get(id=event_id)
            title = event.title
            event.delete()

            ActivityLog.log_activity(
                action_type='event_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted event: {title}',
                request=request
            )

            messages.success(request, f'Event "{title}" has been deleted')
        except Event.DoesNotExist:
            messages.error(request, 'Event not found')

    return redirect('admin_v2_manage_events')


@require_admin_v2_auth
def manage_committees(request):
    """
    Manage all committees
    """
    committees = Committee.objects.annotate(
        member_count=Count('members'),
        chair_count=Count('chairs'),
        document_count=Count('documents')
    ).order_by('name')

    context = {
        'committees': committees,
    }

    return render(request, 'admin_v2/manage_committees.html', context)


@require_admin_v2_auth
def toggle_committee_active(request, committee_id):
    """
    Toggle committee active status
    """
    if request.method == 'POST':
        try:
            committee = Committee.objects.get(id=committee_id)
            committee.is_active = not committee.is_active
            committee.save(update_fields=['is_active'])

            status = "activated" if committee.is_active else "deactivated"
            messages.success(request, f'Committee "{committee.name}" has been {status}')

            ActivityLog.log_activity(
                action_type='committee_status_changed',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} committee: {committee.name}',
                request=request
            )
        except Committee.DoesNotExist:
            messages.error(request, 'Committee not found')

    return redirect('admin_v2_manage_committees')


@require_admin_v2_auth
def manage_users(request):
    """
    Manage all users with filtering
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    admin_filter = request.GET.get('admin', '')
    picture_filter = request.GET.get('picture', '')
    search_query = request.GET.get('search', '')

    # Build query
    users_list = ParliamentUser.objects.order_by('name')

    if status_filter:
        users_list = users_list.filter(member_status=status_filter)

    if type_filter:
        users_list = users_list.filter(member_type=type_filter)

    if admin_filter == 'yes':
        users_list = users_list.filter(is_admin=True)
    elif admin_filter == 'no':
        users_list = users_list.filter(is_admin=False)

    if picture_filter == 'yes':
        users_list = users_list.exclude(profile_picture='').exclude(profile_picture__isnull=True)
    elif picture_filter == 'no':
        users_list = users_list.filter(Q(profile_picture='') | Q(profile_picture__isnull=True))

    if search_query:
        users_list = users_list.filter(
            Q(name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(user_id__icontains=search_query)
        )

    # Paginate
    paginator = Paginator(users_list, 50)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)

    context = {
        'users': users,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'admin_filter': admin_filter,
        'picture_filter': picture_filter,
        'search_query': search_query,
    }

    return render(request, 'admin_v2/manage_users.html', context)


@require_admin_v2_auth
def toggle_user_admin(request, user_id):
    """
    Toggle user admin status
    """
    if request.method == 'POST':
        try:
            user = ParliamentUser.objects.get(user_id=user_id)
            user.is_admin = not user.is_admin
            user.save(update_fields=['is_admin'])

            status = "granted" if user.is_admin else "revoked"
            messages.success(request, f'Admin access {status} for {user.get_display_name()}')

            ActivityLog.log_activity(
                action_type='user_admin_changed',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} admin access for {user.get_display_name()}',
                request=request
            )
        except ParliamentUser.DoesNotExist:
            messages.error(request, 'User not found')

    return redirect('admin_v2_manage_users')


@require_admin_v2_auth
@require_POST
def remove_user_profile_picture(request, user_id):
    """
    Remove a user's profile picture (admin-v2 action)
    """
    try:
        user = ParliamentUser.objects.get(user_id=user_id)

        if user.profile_picture:
            user.profile_picture.delete()
            user.profile_picture_removed_by_admin = True
            user.save(update_fields=['profile_picture', 'profile_picture_removed_by_admin'])

            ActivityLog.log_activity(
                action_type='profile_picture_removed',
                user=request.user,
                description=f'{request.user.get_display_name()} removed profile picture for {user.get_display_name()}',
                request=request,
                object_type='user',
                object_id=user.user_id,
                object_repr=user.get_display_name()
            )

            messages.success(request, f'Profile picture removed for {user.get_display_name()}. User will be notified.')
        else:
            messages.info(request, f'{user.get_display_name()} does not have a profile picture.')

    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found')

    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_manage_users'))


@require_admin_v2_auth
def manage_login_history(request):
    """
    View and manage login history
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    suspicious_filter = request.GET.get('suspicious', '')
    user_search = request.GET.get('user', '')

    # Build query
    logins_list = LoginHistory.objects.select_related('user').defer(*member_defer('user')).order_by('-timestamp')

    if suspicious_filter == 'yes':
        logins_list = logins_list.filter(is_suspicious=True)

    if user_search:
        # Same latent FieldError as above: first_name/last_name don't exist on
        # ParliamentUser. Match the real fields. (v3.15.6)
        logins_list = logins_list.filter(
            Q(user__name__icontains=user_search) |
            Q(user__preferred_name__icontains=user_search) |
            Q(user__username__icontains=user_search)
        )

    # Paginate
    paginator = Paginator(logins_list, 50)
    page_number = request.GET.get('page')
    logins = paginator.get_page(page_number)

    context = {
        'logins': logins,
        'suspicious_filter': suspicious_filter,
        'user_search': user_search,
    }

    return render(request, 'admin_v2/manage_login_history.html', context)


@require_admin_v2_auth
def manage_announcements(request):
    """
    Manage all announcements
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    active_filter = request.GET.get('active', '')

    # Build query
    announcements_list = Announcement.objects.select_related('posted_by').defer(*member_defer('posted_by')).order_by('-posted_at')

    if active_filter == 'yes':
        announcements_list = announcements_list.filter(is_active=True)
    elif active_filter == 'no':
        announcements_list = announcements_list.filter(is_active=False)

    # Paginate
    paginator = Paginator(announcements_list, 25)
    page_number = request.GET.get('page')
    announcements = paginator.get_page(page_number)

    context = {
        'announcements': announcements,
        'active_filter': active_filter,
    }

    return render(request, 'admin_v2/manage_announcements.html', context)


@require_admin_v2_auth
def delete_announcement(request, announcement_id):
    """
    Delete an announcement
    """
    if request.method == 'POST':
        try:
            announcement = Announcement.objects.get(id=announcement_id)
            title = announcement.title
            announcement.delete()

            ActivityLog.log_activity(
                action_type='announcement_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted announcement: {title}',
                request=request
            )

            messages.success(request, f'Announcement "{title}" has been deleted')
        except Announcement.DoesNotExist:
            messages.error(request, 'Announcement not found')

    return redirect('admin_v2_manage_announcements')


@require_admin_v2_auth
def edit_user_profile(request, user_id):
    """
    Admin view to edit a member's profile fields — core info, extended profile,
    big/little brother, and role history.
    """
    from src.models import RoleHistory
    from src.utils.cache_utils import invalidate_user_session_caches
    target = get_object_or_404(ParliamentUser, user_id=user_id)
    all_members = ParliamentUser.objects.exclude(user_id=user_id).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'core':
            target.name = request.POST.get('name', target.name).strip()
            target.preferred_name = request.POST.get('preferred_name', '').strip()
            target.member_type = request.POST.get('member_type', target.member_type)
            target.member_status = request.POST.get('member_status', target.member_status)
            new_email = request.POST.get('email', '').strip()
            if new_email and new_email != target.email:
                if ParliamentUser.objects.filter(email__iexact=new_email).exclude(user_id=user_id).exists():
                    messages.error(request, 'That email is already in use.')
                    return redirect('admin_v2_edit_user_profile', user_id=user_id)
            target.email = new_email or None
            target.phone_number = request.POST.get('phone_number', '').strip()
            target.role_number = request.POST.get('role_number', '').strip() or None
            house = request.POST.get('house', '').strip()
            from src.models import ParliamentUser as PU
            valid_houses = {c[0] for c in PU.HOUSE_CHOICES}
            target.house = house if house in valid_houses else ''
            target.save(update_fields=['name', 'preferred_name', 'member_type', 'member_status', 'email', 'phone_number', 'role_number', 'house'])
            ActivityLog.log_activity(
                action_type='profile_updated',
                user=request.user,
                description=f'{request.user.get_display_name()} edited core profile for {target.get_display_name()}',
                request=request,
                object_type='ParliamentUser',
                object_id=target.pk,
                object_repr=target.name,
            )
            messages.success(request, 'Core info updated.')

        elif action == 'academic_item_add':
            ai_type = request.POST.get('ai_type', '').strip()
            ai_value = request.POST.get('ai_value', '').strip()
            field_map = {'major': 'majors', 'minor': 'minors', 'concentration': 'concentrations'}
            if ai_type in field_map and ai_value:
                field = field_map[ai_type]
                items = list(getattr(target, field) or [])
                if ai_value not in items:
                    items.append(ai_value)
                    setattr(target, field, items)
                    target.save(update_fields=[field])
                    messages.success(request, f'{ai_value} added.')
                else:
                    messages.info(request, f'{ai_value} is already listed.')
            else:
                messages.error(request, 'Type and value are required.')

        elif action == 'academic_item_delete':
            ai_type = request.POST.get('ai_type', '').strip()
            ai_index = request.POST.get('ai_index', '').strip()
            field_map = {'major': 'majors', 'minor': 'minors', 'concentration': 'concentrations'}
            if ai_type in field_map and ai_index.isdigit():
                field = field_map[ai_type]
                items = list(getattr(target, field) or [])
                i = int(ai_index)
                if 0 <= i < len(items):
                    items.pop(i)
                    setattr(target, field, items)
                    target.save(update_fields=[field])
                    messages.success(request, 'Removed.')

        elif action == 'extended':
            target.about_me = request.POST.get('about_me', '').strip()
            # v3.15.0: canonicalize class + auto-fill its greek from the registry
            from src.pledge_classes import apply_to_fields
            target.pledge_class, target.pledge_class_greek = apply_to_fields(
                request.POST.get('pledge_class', ''),
                request.POST.get('pledge_class_greek', ''))
            target.graduation_semester = request.POST.get('graduation_semester', '').strip()
            raw_year = request.POST.get('graduation_year', '').strip()
            target.graduation_year = int(raw_year) if raw_year.isdigit() else None
            big_bro_id = request.POST.get('big_brother', '').strip()
            target.big_brother = ParliamentUser.objects.get(user_id=big_bro_id) if big_bro_id else None
            target.instagram = request.POST.get('instagram', '').strip().lstrip('@')
            target.twitter = request.POST.get('twitter', '').strip().lstrip('@')
            target.linkedin = request.POST.get('linkedin', '').strip().lstrip('@')
            target.snapchat = request.POST.get('snapchat', '').strip().lstrip('@')
            target.facebook = request.POST.get('facebook', '').strip().lstrip('@')
            other_email = request.POST.get('other_email', '').strip()
            target.other_email = other_email or None
            target.save(update_fields=['about_me', 'pledge_class', 'pledge_class_greek', 'graduation_semester', 'graduation_year', 'big_brother', 'instagram', 'twitter', 'linkedin', 'snapchat', 'facebook', 'other_email'])
            from src.house_utils import inherit_house_from_big
            inherit_house_from_big(target, target.big_brother)
            messages.success(request, 'Extended profile updated.')

        elif action == 'custom_social_add':
            platform = request.POST.get('cs_platform', '').strip()
            handle = request.POST.get('cs_handle', '').strip().lstrip('@')
            if platform and handle:
                socials = list(target.custom_socials or [])
                socials.append({'platform': platform, 'handle': handle})
                target.custom_socials = socials
                target.save(update_fields=['custom_socials'])
                messages.success(request, f'{platform} added.')
            else:
                messages.error(request, 'Platform name and handle are required.')

        elif action == 'custom_social_delete':
            idx = request.POST.get('cs_index', '').strip()
            if idx.isdigit():
                socials = list(target.custom_socials or [])
                i = int(idx)
                if 0 <= i < len(socials):
                    socials.pop(i)
                    target.custom_socials = socials
                    target.save(update_fields=['custom_socials'])
                    messages.success(request, 'Custom social removed.')

        elif action == 'role_history_add':
            role_name = request.POST.get('rh_role_name', '').strip()
            start_sem = request.POST.get('rh_start_semester', '').strip()
            end_sem = request.POST.get('rh_end_semester', '').strip()
            if role_name and start_sem:
                RoleHistory.objects.create(user=target, role_name=role_name, start_semester=start_sem, end_semester=end_sem)
                messages.success(request, 'Role history entry added.')
            else:
                messages.error(request, 'Role name and start semester are required.')

        elif action == 'role_history_delete':
            rh_id = request.POST.get('rh_id', '').strip()
            if rh_id:
                RoleHistory.objects.filter(id=rh_id, user=target).delete()
                messages.success(request, 'Role history entry removed.')

        elif action == 'initiation_chapter_add':
            school = request.POST.get('ic_school', '').strip()
            chapter = request.POST.get('ic_chapter', '').strip()
            role_num = request.POST.get('ic_role_number', '').strip()
            if school and chapter:
                chapters = list(target.initiation_chapters or [])
                entry = {'school': school, 'chapter': chapter}
                if role_num:
                    entry['role_number'] = role_num
                chapters.append(entry)
                target.initiation_chapters = chapters
                target.save(update_fields=['initiation_chapters'])
                messages.success(request, f'{chapter} at {school} added.')
            else:
                messages.error(request, 'School and chapter name are required.')

        elif action == 'initiation_chapter_delete':
            idx = request.POST.get('ic_index', '').strip()
            if idx.isdigit():
                chapters = list(target.initiation_chapters or [])
                i = int(idx)
                if 0 <= i < len(chapters):
                    chapters.pop(i)
                    target.initiation_chapters = chapters
                    target.save(update_fields=['initiation_chapters'])
                    messages.success(request, 'Initiation chapter removed.')

        # Bust per-user caches so any context-processor data reflects the edit immediately.
        invalidate_user_session_caches(target.pk)
        return redirect('admin_v2_edit_user_profile', user_id=user_id)

    role_histories = RoleHistory.objects.filter(user=target)
    academic_sections = [
        ('Major', 'major', list(target.majors or [])),
        ('Minor', 'minor', list(target.minors or [])),
        ('Concentration', 'concentration', list(target.concentrations or [])),
    ]
    from src.pledge_classes import all_classes
    return render(request, 'admin_v2/edit_user_profile.html', {
        'target': target,
        'all_members': all_members,
        'role_histories': role_histories,
        'academic_sections': academic_sections,
        'pledge_class_choices': all_classes(),
    })


@require_admin_v2_auth
def user_login_security(request, user_id):
    """
    Detailed login security view for a specific user
    Shows login history, alerts, IP addresses, and security controls
    """
    user = get_object_or_404(ParliamentUser, user_id=user_id)

    # Get login history
    login_history = LoginHistory.objects.filter(user=user).order_by('-timestamp')[:50]

    # Get security alerts (limited to last 25)
    alerts = LoginAlert.objects.filter(user=user).order_by('-created_at')[:25]

    # Get unique IPs from login history — batch whitelist/blacklist lookups to avoid N+1
    all_ips = {login.ip_address for login in login_history if login.ip_address}
    whitelisted_ips = set(IPWhitelist.objects.filter(ip_address__in=all_ips, is_active=True).values_list('ip_address', flat=True))
    blacklisted_ips = set(IPBlacklist.objects.filter(ip_address__in=all_ips, is_active=True).values_list('ip_address', flat=True))

    unique_ips = set()
    ip_info = []
    for login in login_history:
        ip = login.ip_address
        if ip and ip not in unique_ips:
            unique_ips.add(ip)
            ip_info.append({
                'ip': ip,
                'location': login.location_display,
                'last_used': login.timestamp,
                'is_whitelisted': ip in whitelisted_ips,
                'is_blacklisted': ip in blacklisted_ips,
                'risk_level': login.risk_level,
            })

    # Statistics (query separately to avoid slicing issues)
    stats = {
        'total_logins': LoginHistory.objects.filter(user=user).count(),
        'failed_logins': LoginHistory.objects.filter(user=user, status='failed').count(),
        'suspicious_logins': LoginHistory.objects.filter(user=user, is_suspicious=True).count(),
        'active_alerts': LoginAlert.objects.filter(user=user, status='new').count(),
        'unique_ips': len(unique_ips),
        'unique_locations': len(set(login.location_display for login in login_history if login.city)),
    }

    # Check if there's a temporary password to display from session
    temp_password_data = request.session.pop('temp_password_display', None)

    # Watch flag
    try:
        watch_flag = UserWatchFlag.objects.get(user=user)
    except UserWatchFlag.DoesNotExist:
        watch_flag = None

    # Activity log for this user with optional filters
    activity_category = request.GET.get('activity_category', '')
    activity_date_range = request.GET.get('activity_date', '30')

    user_activity = ActivityLog.objects.filter(user=user).order_by('-timestamp')

    if activity_category:
        user_activity = user_activity.filter(action_category=activity_category)

    now_ts = timezone.now()
    if activity_date_range == '1':
        user_activity = user_activity.filter(timestamp__gte=now_ts - timedelta(days=1))
    elif activity_date_range == '7':
        user_activity = user_activity.filter(timestamp__gte=now_ts - timedelta(days=7))
    elif activity_date_range == '30':
        user_activity = user_activity.filter(timestamp__gte=now_ts - timedelta(days=30))
    elif activity_date_range == '90':
        user_activity = user_activity.filter(timestamp__gte=now_ts - timedelta(days=90))
    # 'all' shows everything

    user_activity = user_activity[:100]

    activity_total = ActivityLog.objects.filter(user=user).count()

    context = {
        'target_user': user,
        'login_history': login_history,
        'alerts': alerts,
        'ip_info': ip_info,
        'stats': stats,
        'temp_password_data': temp_password_data,
        'watch_flag': watch_flag,
        'user_activity': user_activity,
        'activity_total': activity_total,
        'activity_categories': ActivityLog.ACTION_CATEGORIES,
        'selected_activity_category': activity_category,
        'selected_activity_date': activity_date_range,
    }

    return render(request, 'admin_v2/user_login_security.html', context)


@require_admin_v2_auth
def toggle_watch_flag(request, user_id):
    """Add, update, or remove a watch flag on a user."""
    from django.views.decorators.http import require_POST as _require_POST
    if request.method != 'POST':
        return redirect('admin_v2_user_login_security', user_id=user_id)

    target_user = get_object_or_404(ParliamentUser, user_id=user_id)
    action = request.POST.get('action')
    admin = request.user
    security_logger = logging.getLogger('admin_actions')

    if action == 'add':
        reason = request.POST.get('reason', '').strip()
        notes = request.POST.get('notes', '').strip()
        if not reason:
            messages.error(request, "A reason is required to place a watch flag.")
            return redirect('admin_v2_user_login_security', user_id=user_id)
        flag, created = UserWatchFlag.objects.update_or_create(
            user=target_user,
            defaults={
                'reason': reason,
                'notes': notes,
                'is_active': True,
                'created_by': admin,
            }
        )
        verb = 'placed' if created else 'updated'
        messages.success(request, f"Watch flag {verb} on {target_user.name}.")
        security_logger.warning(
            f"WATCH FLAG {verb.upper()}: {admin.name} ({admin.username}) {verb} watch flag on "
            f"{target_user.name} ({target_user.username}). Reason: {reason}"
        )
        ActivityLog.log_activity(
            action_type='admin_action',
            user=admin,
            description=f"Watch flag {verb} on {target_user.name} ({target_user.username}). Reason: {reason}",
            request=request,
        )

    elif action == 'remove':
        deleted, _ = UserWatchFlag.objects.filter(user=target_user).delete()
        if deleted:
            messages.success(request, f"Watch flag removed from {target_user.name}.")
            security_logger.warning(
                f"WATCH FLAG REMOVED: {admin.name} ({admin.username}) removed watch flag from "
                f"{target_user.name} ({target_user.username})"
            )
            ActivityLog.log_activity(
                action_type='admin_action',
                user=admin,
                description=f"Watch flag removed from {target_user.name} ({target_user.username})",
                request=request,
            )
        else:
            messages.warning(request, "No active watch flag found.")

    elif action == 'deactivate':
        updated = UserWatchFlag.objects.filter(user=target_user).update(is_active=False)
        if updated:
            messages.success(request, f"Watch flag deactivated for {target_user.name} (alerts paused).")
        else:
            messages.warning(request, "No watch flag found.")

    elif action == 'activate':
        updated = UserWatchFlag.objects.filter(user=target_user).update(is_active=True)
        if updated:
            messages.success(request, f"Watch flag reactivated for {target_user.name}.")
        else:
            messages.warning(request, "No watch flag found.")

    return redirect('admin_v2_user_login_security', user_id=user_id)


@require_admin_v2_auth
@require_POST
def force_password_reset(request, user_id):
    """
    Force a user to reset their password
    """
    from django.core.mail import send_mail

    user = get_object_or_404(ParliamentUser, user_id=user_id)
    reason = request.POST.get('reason', 'Security concern flagged by admin')
    password_type = request.POST.get('password_type', 'random')
    send_email = request.POST.get('send_email') == 'true'

    # Determine the new password
    if password_type == 'custom':
        temp_password = request.POST.get('custom_password', '').strip()
        if not temp_password:
            messages.error(request, 'Custom password cannot be empty')
            return redirect('admin_v2_user_login_security', user_id=user_id)
        if len(temp_password) < 9:
            messages.error(request, 'Custom password should be at least 9 characters')
            return redirect('admin_v2_user_login_security', user_id=user_id)
    else:
        # Generate a temporary random password
        temp_password = generate_random_password(length=16)

    # Set the new password
    user.set_password(temp_password)
    user.force_password_change = False  # Allow them to use this password
    user.has_default_password = False
    user.save(update_fields=['password', 'force_password_change', 'has_default_password'])

    # Log the action
    ActivityLog.log_activity(
        action_type='forced_password_reset',
        user=request.user,
        description=f'{request.user.get_display_name()} forced password reset for {user.get_display_name()}. Reason: {reason}',
        request=request,
        object_type='user',
        object_id=user.user_id,
        object_repr=user.get_display_name()
    )

    # Create a security alert for the user
    alert = LoginAlert.objects.create(
        user=user,
        alert_type='other',
        severity='high',
        status='resolved',
        title='Password Reset by Administrator',
        description=f'Your password was reset by an administrator. Reason: {reason}',
        reviewed_by=request.user,
        reviewed_at=timezone.now(),
        resolution_notes=f'New password: {temp_password}',
        user_notified=send_email
    )

    # Send email notification if requested
    email_sent = False
    if send_email and user.email:
        try:
            email_subject = 'Your Parliament Password Has Been Reset'
            email_body = f"""Hello {user.get_display_name()},

Your Parliament account password has been reset by an administrator.

Reason: {reason}

Your new password is: {temp_password}

Please log in using this password. For security reasons, you may want to change it after logging in.

If you did not request this password reset or have any concerns, please contact an administrator immediately.

Best regards,
Parliament Administration Team"""

            send_mail(
                email_subject,
                email_body,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            email_sent = True
        except Exception as e:
            messages.warning(
                request,
                f'Password was reset but email failed to send to {user.email}. Error: {str(e)}. '
                f'New password is stored in security alert resolution notes.'
            )

    # Store the password in request.session to show it only on the next admin panel page
    # This prevents it from showing on login screen if user gets logged out
    if email_sent:
        messages.success(
            request,
            f'Password reset for {user.get_display_name()}. Email sent to {user.email}. '
            f'The new password is also stored in the security alert below for your records.'
        )
    else:
        # Only show password in admin panel context, store it in session temporarily
        request.session['temp_password_display'] = {
            'user': user.get_display_name(),
            'password': temp_password,
            'email': user.email if user.email else None
        }
        if not user.email:
            messages.warning(
                request,
                f'Password reset for {user.get_display_name()}. No email on file - new password will be displayed on next page.'
            )
        elif not send_email:
            messages.info(
                request,
                f'Password reset for {user.get_display_name()}. Email not sent as requested - new password will be displayed on next page.'
            )

    return redirect('admin_v2_user_login_security', user_id=user_id)


@require_admin_v2_auth
@require_POST
def add_ip_to_whitelist(request):
    """
    Add an IP address to the whitelist
    """
    ip_address = request.POST.get('ip_address', '').strip()
    description = request.POST.get('description', '')

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Check if already whitelisted
    if IPWhitelist.objects.filter(ip_address=ip_address, is_active=True).exists():
        messages.warning(request, f'IP {ip_address} is already whitelisted')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Create whitelist entry
    IPWhitelist.objects.create(
        ip_address=ip_address,
        description=description or f'Added by {request.user.get_display_name()}',
        added_by=request.user
    )

    ActivityLog.log_activity(
        action_type='ip_whitelisted',
        user=request.user,
        description=f'{request.user.get_display_name()} added {ip_address} to whitelist: {description}',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been added to whitelist')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def add_ip_to_blacklist(request):
    """
    Add an IP address to the blacklist
    """
    ip_address = request.POST.get('ip_address', '').strip()
    reason = request.POST.get('reason', '')

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Check if already blacklisted
    if IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists():
        messages.warning(request, f'IP {ip_address} is already blacklisted')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Create blacklist entry
    IPBlacklist.objects.create(
        ip_address=ip_address,
        reason=reason or 'Suspicious activity',
        added_by=request.user
    )

    # Immediately invalidate any cached "not blacklisted" result for this IP
    cache.delete(f'ip_blacklisted_{ip_address}')
    # Also set the honeypot ban key so it's blocked without a DB hit for 24h
    cache.set(f'honeypot_ban_{ip_address}', True, 24 * 60 * 60)

    ActivityLog.log_activity(
        action_type='ip_blacklisted',
        user=request.user,
        description=f'{request.user.get_display_name()} blacklisted {ip_address}: {reason}',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been added to blacklist')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def remove_ip_from_whitelist(request):
    """
    Remove an IP address from the whitelist
    """
    ip_address = request.POST.get('ip_address', '').strip()

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Deactivate whitelist entry
    entries = IPWhitelist.objects.filter(ip_address=ip_address, is_active=True)
    count = entries.count()
    entries.update(is_active=False)

    ActivityLog.log_activity(
        action_type='ip_whitelist_removed',
        user=request.user,
        description=f'{request.user.get_display_name()} removed {ip_address} from whitelist',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been removed from whitelist ({count} entries deactivated)')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def remove_ip_from_blacklist(request):
    """
    Remove an IP address from the blacklist
    """
    ip_address = request.POST.get('ip_address', '').strip()

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Deactivate blacklist entry
    entries = IPBlacklist.objects.filter(ip_address=ip_address, is_active=True)
    count = entries.count()
    entries.update(is_active=False)

    # Clear cached block state so the next request re-checks the DB
    cache.delete(f'ip_blacklisted_{ip_address}')

    ActivityLog.log_activity(
        action_type='ip_blacklist_removed',
        user=request.user,
        description=f'{request.user.get_display_name()} removed {ip_address} from blacklist',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been removed from blacklist ({count} entries deactivated)')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
def manage_ip_whitelist(request):
    """
    Manage IP whitelist entries
    """
    whitelist_entries = IPWhitelist.objects.filter(is_active=True).order_by('-added_at')

    context = {
        'whitelist_entries': whitelist_entries,
    }

    return render(request, 'admin_v2/ip_whitelist.html', context)


@require_admin_v2_auth
def manage_ip_blacklist(request):
    """
    Manage IP blacklist entries
    """
    blacklist_entries = IPBlacklist.objects.filter(is_active=True).order_by('-added_at')

    context = {
        'blacklist_entries': blacklist_entries,
    }

    return render(request, 'admin_v2/ip_blacklist.html', context)


@require_admin_v2_auth
def manage_security_alerts(request):
    """
    Manage security alerts across all users
    """
    # Filter parameters
    status_filter = request.GET.get('status', '')
    severity_filter = request.GET.get('severity', '')

    # v3.17.3: `login_history` was joined and never read — security_alerts.html
    # renders the alert and the member, not the LoginHistory row.
    alerts = LoginAlert.objects.select_related('user').defer(*member_defer('user')).order_by('-created_at')

    if status_filter:
        alerts = alerts.filter(status=status_filter)
    if severity_filter:
        alerts = alerts.filter(severity=severity_filter)

    # Statistics
    stats = {
        'total_alerts': LoginAlert.objects.count(),
        'new_alerts': LoginAlert.objects.filter(status='new').count(),
        'investigating': LoginAlert.objects.filter(status='investigating').count(),
        'critical_alerts': LoginAlert.objects.filter(severity='critical', status='new').count(),
        'high_alerts': LoginAlert.objects.filter(severity='high', status='new').count(),
    }

    context = {
        'alerts': alerts[:100],  # Limit to 100 most recent
        'stats': stats,
        'status_filter': status_filter,
        'severity_filter': severity_filter,
    }

    return render(request, 'admin_v2/security_alerts.html', context)


@require_admin_v2_auth
def dismiss_alert(request, alert_id):
    """Dismiss a single security alert (mark as resolved)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        alert = LoginAlert.objects.get(id=alert_id)
        alert.status = 'resolved'
        alert.reviewed_by = request.user
        alert.reviewed_at = timezone.now()
        alert.resolution_notes = request.POST.get('notes', 'Dismissed by admin')
        alert.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'resolution_notes'])
        return JsonResponse({'success': True, 'new_count': LoginAlert.objects.filter(status='new').count()})
    except LoginAlert.DoesNotExist:
        return JsonResponse({'error': 'Alert not found'}, status=404)


@require_admin_v2_auth
def dismiss_all_alerts(request):
    """Dismiss all new security alerts."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    count = LoginAlert.objects.filter(status='new').update(
        status='resolved',
        reviewed_by=request.user,
        reviewed_at=timezone.now(),
        resolution_notes='Bulk dismissed by admin',
    )
    ActivityLog.log_activity(
        action_type='security',
        user=request.user,
        description=f'{request.user.get_display_name()} bulk-dismissed {count} security alert(s)',
        request=request,
    )
    return JsonResponse({'success': True, 'dismissed': count})


@require_admin_v2_auth
def send_test_announcement_email(request):
    """
    Send a test announcement email to the current user.
    Uses the same template and formatting as real announcement emails.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    if request.method != 'POST':
        messages.error(request, 'Invalid request method')
        return redirect('admin_v2_dashboard')

    user = request.user

    # Check if user has an email set
    if not user.email:
        messages.error(request, 'You do not have an email address set. Please add one in your profile first.')
        return redirect('admin_v2_dashboard')

    # Create a mock announcement object for testing
    class MockAnnouncement:
        def __init__(self):
            self.id = 0
            self.title = "Test Announcement - Email System Check"
            self.content = """This is a TEST email from the Alpha Mu Parliament system.

If you are receiving this email, it means the announcement email system is working correctly!

This email was sent from the Admin-v2 dashboard to verify email delivery and formatting before the demo.

Test details:
• Email template: announcement_notification.html
• Tracking pixel: Included (pointing to test endpoint)
• HTML formatting: Enabled
• Plain text fallback: Included

-- This is an automated test message --"""
            self.posted_at = timezone.now()
            self.posted_by = user
            self.event_date = None  # No event date for test

    mock_announcement = MockAnnouncement()

    # Get site URL
    site_url = getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')

    # Generate tracking URL (will be a test/invalid one)
    tracking_url = f"{site_url}/track/announcement/0/user/{user.user_id}/"

    try:
        # Create HTML email with tracking pixel
        html_message = render_to_string('emails/announcement_notification.html', {
            'announcement': mock_announcement,
            'site_url': site_url,
            'tracking_url': tracking_url,
            'user': user,
        })

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send the email
        msg = EmailMultiAlternatives(
            subject="[TEST] New Announcement: Test Announcement - Email System Check",
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email]
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send()

        # Log the activity
        ActivityLog.objects.create(
            user=user,
            action_category='settings',
            action_type='settings_changed',
            description=f'Sent test announcement email to {user.email}',
            ip_address=get_client_ip(request)
        )

        messages.success(request, f'Test email sent successfully to {user.email}! Check your inbox (and spam folder).')

    except Exception as e:
        messages.error(request, f'Failed to send test email: {str(e)}')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def check_default_password(request, user_id):
    """
    API endpoint to check if a user has a default password.
    Done on-demand to avoid expensive password hashing on every page load.
    """
    from django.http import JsonResponse
    import re

    try:
        user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    matched_pattern = None
    patterns_checked = []

    # Build list of patterns to check
    patterns_to_check = []

    # Pattern: first initial + last name + user_id (e.g., "aboggs69")
    if user.name:
        parts = user.name.strip().split()
        if len(parts) >= 1:
            first_initial = parts[0][0].lower() if parts[0] else ''
            last_name = parts[-1].lower() if len(parts) > 1 else parts[0].lower()

            # Clean version (remove special chars like periods, commas)
            clean_last = re.sub(r'[^a-z0-9]', '', last_name)
            base_pattern = first_initial + clean_last

            # PRIMARY PATTERN: first initial + last name + user_id
            patterns_to_check.append(base_pattern + str(user.user_id))

            # Also check without user_id suffix
            patterns_to_check.append(base_pattern)

            # With "1" suffix
            patterns_to_check.append(base_pattern + '1')

    # User ID alone
    if user.user_id:
        patterns_to_check.append(str(user.user_id))

    # Remove duplicates while preserving order
    seen = set()
    unique_patterns = []
    for p in patterns_to_check:
        if p and p not in seen:
            seen.add(p)
            unique_patterns.append(p)

    # Check each pattern
    for pattern in unique_patterns:
        patterns_checked.append(pattern)
        if user.check_password(pattern):
            matched_pattern = pattern
            break

    return JsonResponse({
        'success': True,
        'has_default_password': matched_pattern is not None,
        'matched_pattern': matched_pattern,
        'patterns_checked': patterns_checked,  # Debug info
    })


@require_admin_v2_auth
def preview_test_email(request):
    """
    Render the test announcement email in the browser for preview.
    This allows testing the tracking pixel and viewing the email design.
    """
    from django.template.loader import render_to_string
    from django.http import HttpResponse

    user = request.user

    # Create a mock announcement object for testing
    class MockAnnouncement:
        def __init__(self):
            self.id = 0
            self.title = "Test Announcement - Email System Check"
            self.content = """This is a TEST email from the Alpha Mu Parliament system.

If you are receiving this email, it means the announcement email system is working correctly!

This email was sent from the Admin-v2 dashboard to verify email delivery and formatting before the demo.

Test details:
• Email template: announcement_notification.html
• Tracking pixel: Included (pointing to test endpoint)
• HTML formatting: Enabled
• Plain text fallback: Included

-- This is an automated test message --"""
            self.posted_at = timezone.now()
            self.posted_by = user
            self.event_date = None  # No event date for test

    mock_announcement = MockAnnouncement()

    # Get site URL
    site_url = getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')

    # Generate tracking URL (will be a test/invalid one)
    tracking_url = f"{site_url}/track/announcement/0/user/{user.user_id}/"

    # Render the email HTML
    html_content = render_to_string('emails/announcement_notification.html', {
        'announcement': mock_announcement,
        'site_url': site_url,
        'tracking_url': tracking_url,
        'user': user,
    })

    # Log the preview action
    ActivityLog.objects.create(
        user=user,
        action_category='settings',
        action_type='view',
        description='Previewed test announcement email in browser',
        ip_address=get_client_ip(request)
    )

    return HttpResponse(html_content)


def health_check(request):
    """
    Simple health check endpoint for performance monitoring.
    Returns a minimal JSON response with server timestamp.
    This is intentionally not protected by authentication to allow
    accurate latency measurements.
    """
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
    })


@require_admin_v2_auth
def test_email_targeting(request):
    """
    Test announcement email targeting without sending actual emails.
    Shows exactly who would receive an email based on visibility settings.
    """
    results = None
    selected_visibility = []

    if request.method == 'POST':
        # Get selected visibility options
        selected_visibility = request.POST.getlist('visibility')

        # Get ALL active users for comparison
        all_active_users = ParliamentUser.objects.filter(member_status='Active')

        # Run the same targeting logic as send_announcement_notification
        if selected_visibility:
            member_types = list(selected_visibility)

            # Expand "Member" to include Chair and Officer (same as notification code)
            if 'Member' in member_types:
                member_types.extend(['Chair', 'Officer'])

            # Get all users that match visibility
            all_targeted_users = all_active_users.filter(member_type__in=member_types)

            # Get users EXCLUDED by visibility (wrong member type)
            excluded_by_visibility = all_active_users.exclude(member_type__in=member_types)
        else:
            # No visibility restriction - target all active users
            member_types = ['All Active Users']
            all_targeted_users = all_active_users
            excluded_by_visibility = ParliamentUser.objects.none()

        # Filter to users with valid emails who want notifications
        from django.db.models import Q
        users_with_email = all_targeted_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        # Users who match visibility but won't receive email (no email or notifications disabled)
        users_no_email_or_disabled = all_targeted_users.exclude(
            user_id__in=users_with_email.values_list('user_id', flat=True)
        )

        # Group recipients by member type
        email_recipients_by_type = {}
        for user in users_with_email:
            if user.member_type not in email_recipients_by_type:
                email_recipients_by_type[user.member_type] = []
            email_recipients_by_type[user.member_type].append({
                'name': user.get_display_name(),
                'email': user.email,
                'user_id': user.user_id,
            })

        # Group users who match visibility but have no email/disabled notifications
        no_email_by_type = {}
        for user in users_no_email_or_disabled:
            if user.member_type not in no_email_by_type:
                no_email_by_type[user.member_type] = []

            # Determine reason
            if not user.email:
                reason = 'No email address'
            else:
                # Check if they have preferences and notifications are disabled
                has_prefs = hasattr(user, 'preferences')
                if has_prefs and not user.preferences.email_announcements:
                    reason = 'Email notifications disabled'
                else:
                    reason = 'Unknown'

            no_email_by_type[user.member_type].append({
                'name': user.get_display_name(),
                'email': user.email or '(no email)',
                'user_id': user.user_id,
                'reason': reason,
            })

        # Group users excluded by visibility (wrong member type)
        excluded_by_type = {}
        for user in excluded_by_visibility:
            if user.member_type not in excluded_by_type:
                excluded_by_type[user.member_type] = []
            excluded_by_type[user.member_type].append({
                'name': user.get_display_name(),
                'email': user.email or '(no email)',
                'user_id': user.user_id,
            })

        results = {
            'selected_visibility': selected_visibility or ['All'],
            'expanded_member_types': member_types,
            'total_active_users': all_active_users.count(),
            'total_targeted': all_targeted_users.count(),
            'would_receive_email': users_with_email.count(),
            'would_not_receive_email_issues': users_no_email_or_disabled.count(),
            'excluded_by_visibility': excluded_by_visibility.count(),
            'email_recipients_by_type': email_recipients_by_type,
            'no_email_by_type': no_email_by_type,
            'excluded_by_type': excluded_by_type,
        }

    context = {
        'results': results,
        'selected_visibility': selected_visibility,
        'visibility_options': ['Member', 'Advisor', 'Pledge'],
    }

    return render(request, 'admin_v2/test_email_targeting.html', context)


@require_admin_v2_auth
def email_logs(request):
    """
    View all announcement email logs with detailed send information.
    Also shows pending scheduled announcements that haven't sent emails yet.
    """
    # Get sent email logs
    logs = AnnouncementEmailLog.objects.select_related(
        'announcement', 'initiated_by'
    ).defer(*member_defer('initiated_by')).prefetch_related('recipients').order_by('-created_at')[:50]

    # Get pending scheduled announcements (haven't sent emails yet)
    pending_announcements = Announcement.objects.filter(
        send_email_on_publish=True,
        email_sent_at__isnull=True,
        is_active=True,
    ).select_related('posted_by').defer(*member_defer('posted_by')).order_by('-posted_at')

    context = {
        'logs': logs,
        'pending_announcements': pending_announcements,
    }
    return render(request, 'admin_v2/email_logs.html', context)


@require_admin_v2_auth
def email_log_detail(request, log_id):
    """
    View detailed information about a specific email send.
    Shows all recipients and why they did/didn't receive the email.
    """
    log = get_object_or_404(AnnouncementEmailLog, id=log_id)

    # Group recipients by status
    recipients_by_status = {}
    for recipient in log.recipients.all():
        status = recipient.get_status_display()
        if status not in recipients_by_status:
            recipients_by_status[status] = []
        recipients_by_status[status].append(recipient)

    # Count by status
    status_counts = {}
    for status, recipients in recipients_by_status.items():
        status_counts[status] = len(recipients)

    context = {
        'log': log,
        'recipients_by_status': recipients_by_status,
        'status_counts': status_counts,
    }
    return render(request, 'admin_v2/email_log_detail.html', context)


@require_admin_v2_auth
@require_POST
def send_scheduled_announcement_email(request, announcement_id):
    """
    Manually trigger email send for a scheduled announcement.
    This allows admins to send emails immediately instead of waiting for cron.
    Uses database locking to prevent race conditions with concurrent cron jobs.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        with transaction.atomic():
            # Lock the announcement row and verify it's still pending
            # nowait=True means if the row is locked, raise an error immediately
            announcement = Announcement.objects.select_for_update(
                nowait=True
            ).filter(
                id=announcement_id,
                send_email_on_publish=True,
                email_sent_at__isnull=True,
            ).first()

            if not announcement:
                # Check why it wasn't found
                try:
                    ann = Announcement.objects.get(id=announcement_id)
                    if ann.email_sent_at:
                        messages.error(request, 'Emails have already been sent for this announcement.')
                    elif not ann.send_email_on_publish:
                        messages.error(request, 'This announcement is not scheduled to send emails.')
                    else:
                        messages.error(request, 'Announcement not found.')
                except Announcement.DoesNotExist:
                    messages.error(request, 'Announcement not found.')
                return redirect('admin_v2_email_logs')

            # Mark as sent BEFORE sending (claim the announcement)
            announcement.email_sent_at = timezone.now()
            announcement.send_email_on_publish = False
            announcement.save(update_fields=['email_sent_at', 'send_email_on_publish'])
            announcement_title = announcement.title

        # Now send notifications OUTSIDE the transaction (emails can be slow)
        # The announcement is already marked as sent, so no other job will pick it up
        if announcement.is_published():
            try:
                notify_all_active_members(
                    'announcement',
                    f'New Announcement: {announcement.title}',
                    message=announcement.content[:100],
                    link='/announcements/',
                    source_type='Announcement',
                    source_id=announcement.id,
                )
            except Exception as e:
                logger.error(f"Failed to create in-app notifications for announcement {announcement.id}: {e}")

        # Send email notifications
        sent_count = send_announcement_notification(
            announcement,
            initiated_by=request.user
        )

        messages.success(request, f'Successfully sent {sent_count} email(s) for "{announcement_title}"')
        logger.info(f"Admin manually sent announcement {announcement.id}: {sent_count} emails")

    except transaction.DatabaseError:
        # Row is locked by another process (cron job)
        messages.warning(request, 'This announcement is currently being processed by another job. Please wait.')
    except Exception as e:
        logger.error(f"Failed to send scheduled announcement {announcement_id}: {e}", exc_info=True)
        messages.error(request, f'Failed to send emails: {str(e)}')

    return redirect('admin_v2_email_logs')


# =============================================================================
# Security Management Views
# =============================================================================

@require_admin_v2_auth
def security_dashboard(request):
    """Main security dashboard showing all security tools and alerts."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import (
        QuarantinedAccount, HoneypotAccess, SystemLockdown,
        SecurityNotificationLog, LoginLockout, LoginAlert,
        IPBlacklist, IPWhitelist, CSPViolation,
    )

    now = timezone.now()

    # Quarantines — exclude expired records so the list stays in sync with is_active
    #
    # v3.17.5: materialized. This was a lazy queryset and the template calls
    # `active_quarantines.count` at FOUR places (the card border, the card
    # number, the nav badge and the section badge) before iterating it — and
    # `.count` on a queryset is a fresh `SELECT COUNT(*)` every time, so the
    # page ran five queries for one small list. Dev mode reported it as a 4×
    # repeated shape.
    #
    # Bounded in practice rather than by a slice: the filter is
    # unreleased-and-unexpired, and a member can only be quarantined once at a
    # time, so this is capped by chapter size. Ordered explicitly because a
    # `list()` with no `order_by` has whatever order the backend feels like.
    active_quarantines = list(
        QuarantinedAccount.objects.filter(
            released_at__isnull=True,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )
        .select_related('user', 'quarantined_by')
        .defer(*member_defer('user', 'quarantined_by'))
        .order_by('-quarantined_at')
    )

    # Honeypot
    recent_honeypot = HoneypotAccess.objects.order_by('-accessed_at')[:15]
    honeypot_24h = HoneypotAccess.objects.filter(
        accessed_at__gte=now - timedelta(hours=24)
    ).count()

    # Lockdown
    lockdown = SystemLockdown.get_instance()

    # Security notifications
    recent_notifications = SecurityNotificationLog.objects.order_by('-sent_at')[:8]
    critical_notifications = SecurityNotificationLog.objects.filter(
        severity='critical',
        sent_at__gte=now - timedelta(hours=24)
    ).count()

    # Lockouts
    active_lockouts_qs = LoginLockout.objects.filter(is_cleared=False, expires_at__gt=now)
    active_lockouts_count = active_lockouts_qs.count()
    active_lockouts = active_lockouts_qs.order_by('-locked_at')[:10]

    # Security alerts (LoginAlert)
    new_alerts = LoginAlert.objects.filter(status='new').select_related('user').defer(*member_defer('user')).order_by('-created_at')[:8]
    new_alerts_count = LoginAlert.objects.filter(status='new').count()
    critical_alerts_count = LoginAlert.objects.filter(
        status='new', severity__in=['critical', 'high']
    ).count()

    # IP lists
    blacklist_count = IPBlacklist.objects.filter(is_active=True).count()
    whitelist_count = IPWhitelist.objects.filter(is_active=True).count()
    blacklisted_ips = set(IPBlacklist.objects.filter(is_active=True).values_list('ip_address', flat=True))

    # CSP violations
    csp_violation_count = CSPViolation.objects.filter(dismissed=False).count()

    return render(request, 'admin_v2/security_dashboard.html', {
        'active_quarantines': active_quarantines,
        'recent_honeypot': recent_honeypot,
        'honeypot_24h': honeypot_24h,
        'lockdown': lockdown,
        'recent_notifications': recent_notifications,
        'critical_notifications': critical_notifications,
        'active_lockouts': active_lockouts,
        'active_lockouts_count': active_lockouts_count,
        'new_alerts': new_alerts,
        'new_alerts_count': new_alerts_count,
        'critical_alerts_count': critical_alerts_count,
        'blacklist_count': blacklist_count,
        'whitelist_count': whitelist_count,
        'blacklisted_ips': blacklisted_ips,
        'csp_violation_count': csp_violation_count,
    })


@require_admin_v2_auth
def quarantine_management(request):
    """Manage quarantined accounts."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import QuarantinedAccount

    if request.method == 'POST':
        action = request.POST.get('action')
        quarantine_id = request.POST.get('quarantine_id')

        if action == 'release' and quarantine_id:
            try:
                quarantine = QuarantinedAccount.objects.get(pk=quarantine_id)
                notes = request.POST.get('release_notes', '')
                quarantine.release(request.user, notes)
                messages.success(request, f'Released quarantine for {quarantine.user.name}')
                logger.info(f"Admin {request.user.username} released quarantine for {quarantine.user.name}")
                log_admin_action(
                    actor=request.user, action='quarantine_lifted', request=request,
                    target_user=quarantine.user, target_repr=str(quarantine.user),
                    detail=f"Release notes: {notes}" if notes else '',
                )
            except QuarantinedAccount.DoesNotExist:
                messages.error(request, 'Quarantine record not found')

        elif action == 'quarantine_user':
            from src.models import ParliamentUser
            user_id = request.POST.get('user_id')
            reason = request.POST.get('reason', 'Manual quarantine by admin')
            ip_address = request.POST.get('ip_address', '0.0.0.0')  # nosec B104 - placeholder default IP for the quarantine record, not a socket bind
            expires_at_str = request.POST.get('expires_at', '').strip()

            # Parse optional expiry datetime
            expires_at = None
            if expires_at_str:
                try:
                    from django.utils.dateparse import parse_datetime
                    from django.utils import timezone as tz
                    parsed = parse_datetime(expires_at_str)
                    if parsed:
                        expires_at = tz.make_aware(parsed) if tz.is_naive(parsed) else parsed
                except Exception:
                    pass  # Invalid date — treat as no expiry

            try:
                user = ParliamentUser.objects.get(user_id=user_id)
                QuarantinedAccount.quarantine_user(
                    user=user,
                    ip_address=ip_address,
                    reason=reason,
                    admin=request.user,
                    expires_at=expires_at,
                )
                expiry_msg = f" (expires {expires_at.strftime('%Y-%m-%d %H:%M')})" if expires_at else ''
                messages.success(request, f'Quarantined account: {user.name}{expiry_msg}')
                logger.info(f"Admin {request.user.username} quarantined {user.name}: {reason}{expiry_msg}")
                log_admin_action(
                    actor=request.user, action='quarantine_set', request=request,
                    target_user=user, target_repr=str(user),
                    detail=f"Reason: {reason}; IP: {ip_address}{expiry_msg}",
                )

                # Send alert
                from src.security_notifications import alert_account_quarantined
                alert_account_quarantined(user, ip_address, reason, is_auto=False)

            except ParliamentUser.DoesNotExist:
                messages.error(request, 'User not found')

        return redirect('admin_v2_quarantine')

    # Get all quarantine records — exclude expired so UI matches is_active property
    #
    # v3.17.5: materialized, same reason as `security_dashboard` above —
    # `quarantine_management.html` calls `.count` for the heading, tests the
    # queryset for truthiness, then iterates it: three queries for one list.
    _now = timezone.now()
    active_quarantines = list(
        QuarantinedAccount.objects.filter(
            released_at__isnull=True,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=_now)
        )
        .select_related('user', 'quarantined_by')
        .defer(*member_defer('user', 'quarantined_by'))
        .order_by('-quarantined_at')
    )

    released_quarantines = QuarantinedAccount.objects.filter(
        released_at__isnull=False
    ).select_related('user', 'quarantined_by', 'released_by').defer(*member_defer('user', 'quarantined_by', 'released_by')).order_by('-released_at')[:50]

    # Get list of users that can be quarantined. All member statuses are
    # selectable (was is_active=True only — you couldn't quarantine an
    # inactive/alumni/removed account even though those can still hold
    # credentials); non-Active members are labeled with their status in the
    # dropdown. (v3.15.7, Mason 07-23)
    from src.models import ParliamentUser
    selectable_users = ParliamentUser.objects.filter(
        is_quarantined=False
    ).order_by('member_status', 'name')

    return render(request, 'admin_v2/quarantine_management.html', {
        'active_quarantines': active_quarantines,
        'released_quarantines': released_quarantines,
        'selectable_users': selectable_users,
    })


@require_admin_v2_auth
def lockdown_control(request):
    """Control emergency lockdown mode."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import SystemLockdown

    lockdown = SystemLockdown.get_instance()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'activate':
            reason = request.POST.get('reason', 'Emergency lockdown activated')
            whitelisted_ips_str = request.POST.get('whitelisted_ips', '')
            whitelisted_ips = [ip.strip() for ip in whitelisted_ips_str.split(',') if ip.strip()]

            lockdown.activate(request.user, reason, whitelisted_ips)
            messages.warning(request, 'EMERGENCY LOCKDOWN ACTIVATED. All non-whitelisted access is blocked.')
            logger.critical(f"LOCKDOWN ACTIVATED by {request.user.username}: {reason}")

            # Send alert
            from src.security_notifications import alert_lockdown_activated
            alert_lockdown_activated(request.user, reason)

        elif action == 'deactivate':
            lockdown.deactivate(request.user)
            messages.success(request, 'Emergency lockdown has been deactivated. Normal operations resumed.')
            logger.info(f"Lockdown deactivated by {request.user.username}")

            # Send alert
            from src.security_notifications import alert_lockdown_deactivated
            alert_lockdown_deactivated(request.user)

        elif action == 'update_whitelist':
            whitelisted_ips_str = request.POST.get('whitelisted_ips', '')
            lockdown.whitelisted_ips = [ip.strip() for ip in whitelisted_ips_str.split(',') if ip.strip()]
            lockdown.save(update_fields=['whitelisted_ips'])
            messages.success(request, 'Whitelist updated')
            logger.info(f"Lockdown whitelist updated by {request.user.username}")

        elif action == 'update_message':
            lockdown.message = request.POST.get('message', lockdown.message)
            lockdown.save(update_fields=['message'])
            messages.success(request, 'Lockdown message updated')

        return redirect('admin_v2_lockdown')

    # Get current IP for whitelisting suggestion
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        current_ip = x_forwarded_for.split(',')[-1].strip()
    else:
        current_ip = request.META.get('REMOTE_ADDR', 'unknown')

    return render(request, 'admin_v2/lockdown_control.html', {
        'lockdown': lockdown,
        'current_ip': current_ip,
    })


@require_admin_v2_auth
def honeypot_logs(request):
    """View honeypot access logs."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import HoneypotAccess
    from django.core.paginator import Paginator
    from django.db.models import Count

    logs = HoneypotAccess.objects.order_by('-accessed_at')

    # Filters
    endpoint_filter = request.GET.get('endpoint', '')
    ip_filter = request.GET.get('ip', '')
    if endpoint_filter:
        logs = logs.filter(endpoint__icontains=endpoint_filter)
    if ip_filter:
        logs = logs.filter(ip_address=ip_filter)

    total_filtered = logs.count()

    # Paginate
    paginator = Paginator(logs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Get stats
    total_24h = HoneypotAccess.objects.filter(
        accessed_at__gte=timezone.now() - timedelta(hours=24)
    ).count()
    total_all = HoneypotAccess.objects.count()

    # Most targeted endpoints
    top_endpoints = HoneypotAccess.objects.values('endpoint').annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    # Top attacking IPs
    top_ips = HoneypotAccess.objects.values('ip_address').annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    # Currently blacklisted IPs (for showing block status per row)
    blacklisted_ips = set(
        IPBlacklist.objects.filter(is_active=True).values_list('ip_address', flat=True)
    )

    return render(request, 'admin_v2/honeypot_logs.html', {
        'logs': page_obj,
        'endpoint_filter': endpoint_filter,
        'ip_filter': ip_filter,
        'total_24h': total_24h,
        'total_all': total_all,
        'total_filtered': total_filtered,
        'top_endpoints': top_endpoints,
        'top_ips': top_ips,
        'blacklisted_ips': blacklisted_ips,
    })


@require_admin_v2_auth
@require_POST
def delete_honeypot_log(request, log_id):
    """Delete a single honeypot log entry."""
    if not request.user.is_admin:
        return JsonResponse({'success': False, 'error': 'Admin access required'}, status=403)
    from src.models import HoneypotAccess
    try:
        entry = HoneypotAccess.objects.get(id=log_id)
        entry.delete()
        return JsonResponse({'success': True})
    except HoneypotAccess.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Log not found'}, status=404)


@require_admin_v2_auth
@require_POST
def clear_honeypot_logs(request):
    """Bulk delete honeypot logs, optionally filtered by IP."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")
    from src.models import HoneypotAccess
    ip_address = request.POST.get('ip_address', '').strip()
    if ip_address:
        deleted, _ = HoneypotAccess.objects.filter(ip_address=ip_address).delete()
        if deleted:
            messages.success(request, f'Deleted {deleted} log entr{"ies" if deleted != 1 else "y"} for {ip_address}.')
        else:
            messages.info(request, f'No logs found for {ip_address}.')
    else:
        deleted, _ = HoneypotAccess.objects.all().delete()
        if deleted:
            messages.success(request, f'Cleared {deleted} honeypot log entr{"ies" if deleted != 1 else "y"}.')
        else:
            messages.info(request, 'No honeypot logs to clear.')
    return redirect('admin_v2_honeypot_logs')


@require_admin_v2_auth
@require_POST
def blacklist_all_honeypot_ips(request):
    """Blacklist every unique honeypot IP that is not already blacklisted."""
    if not request.user.is_admin:
        return JsonResponse({'success': False, 'error': 'Admin access required'}, status=403)

    from src.models import HoneypotAccess

    already_blocked = set(
        IPBlacklist.objects.filter(is_active=True).values_list('ip_address', flat=True)
    )
    all_honeypot_ips = set(
        HoneypotAccess.objects.values_list('ip_address', flat=True).distinct()
    )
    to_block = all_honeypot_ips - already_blocked

    created = 0
    for ip in to_block:
        IPBlacklist.objects.create(
            ip_address=ip,
            reason='Honeypot trigger — bulk blacklist',
            added_by=request.user,
        )
        cache.delete(f'ip_blacklisted_{ip}')
        cache.set(f'honeypot_ban_{ip}', True, 24 * 60 * 60)
        created += 1

    if created:
        ActivityLog.log_activity(
            action_type='ip_blacklisted',
            user=request.user,
            description=f'{request.user.get_display_name()} bulk-blacklisted {created} honeypot IP(s)',
            request=request,
        )
        messages.success(request, f'{created} IP address{"es" if created != 1 else ""} blacklisted.')
    else:
        messages.info(request, 'All honeypot IPs are already blacklisted.')

    return redirect('admin_v2_honeypot_logs')


@require_admin_v2_auth
def manage_lockouts(request):
    """View and manage active login lockouts."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import LoginLockout, IPBlacklist, IPWhitelist
    from django.core.cache import cache
    from django.utils import timezone

    if request.method == 'POST':
        action = request.POST.get('action')
        lockout_id = request.POST.get('lockout_id', '').strip()
        ip_address = request.POST.get('ip_address', '').strip()

        if action == 'clear' and lockout_id:
            try:
                lockout = LoginLockout.objects.get(pk=lockout_id)
                ip = lockout.ip_address
                username = lockout.username

                # Clear cache keys for all three systems
                cache.delete(f'login_lockout_{ip}')
                cache.delete(f'login_attempts_{ip}')
                cache.delete(f'login_lockout_ip_{ip}')
                cache.delete(f'login_attempts_ip_{ip}')
                if username:
                    cache.delete(f'login_lockout_user_{username}')
                    cache.delete(f'login_attempts_user_{username}')
                # Also clear whitelist cache so it refreshes
                cache.delete(f'ip_whitelist_{ip}')

                lockout.is_cleared = True
                lockout.cleared_at = timezone.now()
                lockout.cleared_by = request.user
                lockout.save(update_fields=['is_cleared', 'cleared_at', 'cleared_by'])

                messages.success(request, f'Lockout cleared for {ip}.')
                logger.info(f"Admin {request.user.username} cleared lockout for IP {ip}")
            except LoginLockout.DoesNotExist:
                messages.error(request, 'Lockout record not found.')

        elif action == 'blacklist' and lockout_id:
            try:
                lockout = LoginLockout.objects.get(pk=lockout_id)
                ip = lockout.ip_address

                # Add to blacklist
                existing = IPBlacklist.objects.filter(ip_address=ip).first()
                if existing:
                    existing.is_active = True
                    existing.reason = f'Blacklisted from lockout management by {request.user.username}'
                    existing.save(update_fields=['is_active', 'reason'])
                else:
                    IPBlacklist.objects.create(
                        ip_address=ip,
                        reason=f'Repeated login failures — blacklisted by {request.user.username}',
                        added_by=request.user,
                        is_active=True,
                    )

                # Mark lockout as cleared too
                lockout.is_cleared = True
                lockout.cleared_at = timezone.now()
                lockout.cleared_by = request.user
                lockout.save(update_fields=['is_cleared', 'cleared_at', 'cleared_by'])

                messages.success(request, f'IP {ip} has been blacklisted.')
                logger.info(f"Admin {request.user.username} blacklisted IP {ip} from lockout management")
            except LoginLockout.DoesNotExist:
                messages.error(request, 'Lockout record not found.')

        elif action == 'whitelist_and_clear' and lockout_id:
            try:
                lockout = LoginLockout.objects.get(pk=lockout_id)
                ip = lockout.ip_address

                # Add to whitelist
                existing = IPWhitelist.objects.filter(ip_address=ip).first()
                if existing:
                    existing.is_active = True
                    existing.save(update_fields=['is_active'])
                else:
                    IPWhitelist.objects.create(
                        ip_address=ip,
                        description=f'Whitelisted from lockout management by {request.user.username}',
                        added_by=request.user,
                        is_active=True,
                    )

                # Clear all cache lockout keys
                cache.delete(f'login_lockout_{ip}')
                cache.delete(f'login_attempts_{ip}')
                cache.delete(f'login_lockout_ip_{ip}')
                cache.delete(f'login_attempts_ip_{ip}')
                cache.delete(f'ip_whitelist_{ip}')

                lockout.is_cleared = True
                lockout.cleared_at = timezone.now()
                lockout.cleared_by = request.user
                lockout.save(update_fields=['is_cleared', 'cleared_at', 'cleared_by'])

                messages.success(request, f'IP {ip} has been whitelisted and lockout cleared.')
                logger.info(f"Admin {request.user.username} whitelisted IP {ip} from lockout management")
            except LoginLockout.DoesNotExist:
                messages.error(request, 'Lockout record not found.')

        elif action == 'clear_all_active':
            to_clear = list(LoginLockout.objects.filter(
                is_cleared=False,
                expires_at__gt=timezone.now()
            ))
            now = timezone.now()
            for lockout in to_clear:
                cache.delete(f'login_lockout_{lockout.ip_address}')
                cache.delete(f'login_attempts_{lockout.ip_address}')
                cache.delete(f'login_lockout_ip_{lockout.ip_address}')
                cache.delete(f'login_attempts_ip_{lockout.ip_address}')
                if lockout.username:
                    cache.delete(f'login_lockout_user_{lockout.username}')
                    cache.delete(f'login_attempts_user_{lockout.username}')
                cache.delete(f'ip_whitelist_{lockout.ip_address}')
                lockout.is_cleared = True
                lockout.cleared_at = now
                lockout.cleared_by = request.user
                lockout.save(update_fields=['is_cleared', 'cleared_at', 'cleared_by'])
            messages.success(request, f'Cleared {len(to_clear)} active lockout(s).')
            logger.info(f"Admin {request.user.username} bulk-cleared {len(to_clear)} lockouts")

        return redirect('admin_v2_lockouts')

    # GET: show lockouts
    active_lockouts = LoginLockout.objects.filter(
        is_cleared=False,
        expires_at__gt=timezone.now()
    ).order_by('-locked_at')

    expired_lockouts = LoginLockout.objects.filter(
        is_cleared=False,
        expires_at__lte=timezone.now()
    ).order_by('-locked_at')[:20]

    recent_cleared = LoginLockout.objects.filter(
        is_cleared=True
    ).order_by('-cleared_at')[:20]

    return render(request, 'admin_v2/lockouts.html', {
        'active_lockouts': active_lockouts,
        'expired_lockouts': expired_lockouts,
        'recent_cleared': recent_cleared,
        'active_count': active_lockouts.count(),
    })


@require_admin_v2_auth
def security_notifications_log(request):
    """View security notification history."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import SecurityNotificationLog
    from django.core.paginator import Paginator

    notifications = SecurityNotificationLog.objects.order_by('-sent_at')

    # Filter by severity if specified
    severity_filter = request.GET.get('severity', '')
    if severity_filter:
        notifications = notifications.filter(severity=severity_filter)

    # Filter by event type if specified
    event_filter = request.GET.get('event_type', '')
    if event_filter:
        notifications = notifications.filter(event_type__icontains=event_filter)

    # Paginate
    paginator = Paginator(notifications, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Get distinct event types for filter dropdown
    event_types = SecurityNotificationLog.objects.values_list(
        'event_type', flat=True
    ).distinct()

    return render(request, 'admin_v2/security_notifications.html', {
        'notifications': page_obj,
        'severity_filter': severity_filter,
        'event_filter': event_filter,
        'event_types': event_types,
    })


# ---------------------------------------------------------------------------
# Admin Action Audit Log Viewer
# ---------------------------------------------------------------------------

@require_admin_v2_auth
def audit_log(request):
    """
    Browse and filter the AdminActionLog — the officer-facing audit trail.
    Supports filtering by action type, actor, and date range, with pagination.
    Add ?export=csv to download the current filtered set as a CSV file.
    """
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import AdminActionLog
    from django.core.paginator import Paginator

    qs = AdminActionLog.objects.select_related('actor', 'target_user').defer(*member_defer('actor', 'target_user')).order_by('-timestamp')

    # Filters
    action_filter = request.GET.get('action', '').strip()
    actor_filter = request.GET.get('actor', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if action_filter:
        qs = qs.filter(action=action_filter)
    if actor_filter:
        qs = qs.filter(actor__user_id=actor_filter)
    if date_from:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(date_from)
            if d:
                qs = qs.filter(timestamp__date__gte=d)
        except Exception:
            pass
    if date_to:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(date_to)
            if d:
                qs = qs.filter(timestamp__date__lte=d)
        except Exception:
            pass

    # CSV export — streams the full filtered set, no pagination
    if request.GET.get('export') == 'csv':
        # v3.17.7: a query-parameter MODE of `admin_v2_audit_log`, so the
        # URL-name list in geo_restriction.py cannot reach it without blocking
        # the log viewer itself. It streams actor, target user, detail and
        # **IP address** for the whole filtered set with no pagination — worth
        # the same treatment as the other bulk exports.
        from src.middleware.geo_restriction import geo_export_blocked
        blocked = geo_export_blocked(request)
        if blocked:
            return blocked

        import csv
        from django.http import StreamingHttpResponse

        def _rows():
            yield ['Timestamp', 'Actor', 'Action', 'Target User', 'Target Repr', 'Detail', 'IP Address']
            for entry in qs.iterator(chunk_size=500):
                yield [
                    entry.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC'),
                    entry.actor.name if entry.actor else '',
                    entry.get_action_display(),
                    entry.target_user.name if entry.target_user else '',
                    entry.target_repr or '',
                    entry.detail or '',
                    entry.ip_address or '',
                ]

        class _Echo:
            def write(self, value):
                return value

        writer = csv.writer(_Echo())
        response = StreamingHttpResponse(
            (writer.writerow(row) for row in _rows()),
            content_type='text/csv',
        )
        from datetime import datetime
        filename = f'audit_log_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Build actor list for filter dropdown (only actors who appear in the log)
    actors = (
        ParliamentUser.objects
        .filter(admin_actions_taken__isnull=False)
        .distinct()
        .order_by('name')
    )

    return render(request, 'admin_v2/audit_log.html', {
        'page_obj': page_obj,
        'action_choices': AdminActionLog.ACTION_CHOICES,
        'actors': actors,
        'action_filter': action_filter,
        'actor_filter': actor_filter,
        'date_from': date_from,
        'date_to': date_to,
        'total_count': qs.count(),
    })


# ---------------------------------------------------------------------------
# CSP Violation Analytics
# ---------------------------------------------------------------------------

@require_admin_v2_auth
def csp_violations(request):
    """Analytics dashboard for CSP violation reports."""
    if not request.user.is_admin:
        return HttpResponseForbidden("Admin access required")

    from src.models import CSPViolation
    from django.db.models import Count, Max

    show_dismissed = request.GET.get('show_dismissed') == '1'

    qs = CSPViolation.objects.all() if show_dismissed else CSPViolation.objects.filter(dismissed=False)

    # Group by (violated_directive, blocked_uri) — one row per unique violation type
    groups = (
        qs
        .values('violated_directive', 'blocked_uri')
        .annotate(count=Count('id'), latest=Max('created_at'))
        .order_by('-latest')
    )

    total_undismissed = CSPViolation.objects.filter(dismissed=False).count()

    return render(request, 'admin_v2/csp_violations.html', {
        'groups': groups,
        'show_dismissed': show_dismissed,
        'total_undismissed': total_undismissed,
    })


@require_admin_v2_auth
@require_POST
def csp_violation_dismiss(request):
    """Dismiss all CSP violations matching a (violated_directive, blocked_uri) pair."""
    if not request.user.is_admin:
        return JsonResponse({'ok': False, 'error': 'Admin access required'}, status=403)

    from src.models import CSPViolation

    directive = request.POST.get('violated_directive', '')
    blocked   = request.POST.get('blocked_uri', '')

    if not directive and not blocked:
        return JsonResponse({'ok': False, 'error': 'Missing parameters'}, status=400)

    updated = CSPViolation.objects.filter(
        violated_directive=directive,
        blocked_uri=blocked,
        dismissed=False,
    ).update(dismissed=True, dismissed_at=timezone.now(), dismissed_by=request.user)

    return JsonResponse({'ok': True, 'dismissed': updated})


# ---------------------------------------------------------------------------
# Page Visit Tracking
# ---------------------------------------------------------------------------

@require_POST
def track_page_visit(request):
    """
    Lightweight POST endpoint called via sendBeacon after page load.
    Uses a single-query PostgreSQL upsert to atomically increment the counter.
    Admin-v2 paths are excluded so Mason's own browsing doesn't skew the data.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    path = request.POST.get('path', '').strip()
    if not path or len(path) > 255:
        return JsonResponse({'error': 'Bad request'}, status=400)

    # Skip admin-v2 paths — no point tracking the admin's own usage
    if path.startswith('/admin-v2/') or path.startswith('/admin_v2/'):
        return JsonResponse({'ok': True}, status=200)

    # Single-query atomic upsert (PostgreSQL ON CONFLICT) — avoids the
    # get_or_create+update race condition and halves the round-trips.
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO src_pagevisit (user_id, path, count)
            VALUES (%s, %s, 1)
            ON CONFLICT (user_id, path) DO UPDATE
                SET count = src_pagevisit.count + 1
            """,
            [request.user.pk, path],
        )

    return JsonResponse({'ok': True}, status=200)


def _page_visit_user_q(term):
    """Name-search Q for PageVisit rows. ParliamentUser (AbstractBaseUser) has
    no first_name/last_name fields — the old filter here used
    user__first_name/user__last_name and raised FieldError the moment the
    filter box was actually used. Match the real fields. (v3.15.6)"""
    return (
        Q(user__name__icontains=term) |
        Q(user__preferred_name__icontains=term) |
        Q(user__username__icontains=term)
    )


#: Member-status choices offered by the page-visits dashboard filter (v3.15.7).
_PAGE_VISIT_STATUSES = ('Active', 'Inactive', 'Alumni', 'Removed')


@require_admin_v2_auth
def page_visits_dashboard(request):
    """
    Admin v2 view: aggregated page visit stats, sortable by total visits or user count.
    Supports drilling into a specific path to see per-user breakdown, and
    filtering both views by member name/username (v3.15.6) and by member
    status — Active/Inactive/Alumni/Removed (v3.15.7).
    """
    sort = request.GET.get('sort', 'total')
    drill_path = request.GET.get('path', '').strip()
    user_filter = request.GET.get('user', '').strip()
    status_filter = request.GET.get('status', '').strip()
    if status_filter not in _PAGE_VISIT_STATUSES:
        status_filter = ''  # unknown value -> All statuses

    if drill_path:
        # Per-user breakdown for a specific path
        rows = (
            PageVisit.objects
            .filter(path=drill_path)
            .select_related('user').defer(*member_defer('user'))
            .order_by('-count')
        )
        if user_filter:
            rows = rows.filter(_page_visit_user_q(user_filter))
        if status_filter:
            rows = rows.filter(user__member_status=status_filter)
        return render(request, 'admin_v2/page_visits.html', {
            'drill_path': drill_path,
            'rows': rows,
            'sort': sort,
            'user_filter': user_filter,
            'status_filter': status_filter,
            'status_choices': _PAGE_VISIT_STATUSES,
        })

    # Aggregate by path — optionally restricted to visits by matching member(s),
    # so the dashboard can answer "what has this member been looking at" without
    # drilling path-by-path. Totals/unique counts then reflect only the matched
    # members. (v3.15.6)
    visits = PageVisit.objects.all()
    matched_users = None
    if status_filter:
        visits = visits.filter(user__member_status=status_filter)
    if user_filter:
        visits = visits.filter(_page_visit_user_q(user_filter))
        # Small, bounded list of who matched, for display + disambiguation.
        matched = (
            ParliamentUser.objects
            .filter(page_visits__isnull=False)
            .filter(
                Q(name__icontains=user_filter) |
                Q(preferred_name__icontains=user_filter) |
                Q(username__icontains=user_filter)
            )
        )
        if status_filter:
            matched = matched.filter(member_status=status_filter)
        matched_users = list(matched.distinct().order_by('name')[:20])

    qs = (
        visits
        .values('path')
        .annotate(
            total=Sum('count'),
            unique_users=Count('user', distinct=True),
        )
    )
    if sort == 'users':
        qs = qs.order_by('-unique_users', '-total')
    else:
        qs = qs.order_by('-total', '-unique_users')

    return render(request, 'admin_v2/page_visits.html', {
        'pages': qs,
        'sort': sort,
        'drill_path': None,
        'user_filter': user_filter,
        'status_filter': status_filter,
        'status_choices': _PAGE_VISIT_STATUSES,
        'matched_users': matched_users,
    })


# =============================================================================
# EVENT REMINDER LOGS
# =============================================================================

@require_admin_v2_auth
def event_reminder_logs(request):
    """
    List all event reminder push notification dispatches.
    Shows upcoming events with pending reminders and completed reminder history.
    """
    from django.utils import timezone as tz
    from django.db.models import Q
    now = tz.now()

    logs = EventReminderLog.objects.select_related('event').order_by('-sent_at')[:100]

    # Events with at least one reminder enabled that hasn't fired yet
    pending_events = Event.objects.filter(
        is_active=True,
        date_time__gt=now,
    ).filter(
        Q(reminder_1_enabled=True, reminder_1_sent_at__isnull=True) |
        Q(reminder_2_enabled=True, reminder_2_sent_at__isnull=True)
    ).order_by('date_time')

    return render(request, 'admin_v2/event_reminder_logs.html', {
        'logs': logs,
        'pending_events': pending_events,
        'now': now,
    })


@require_admin_v2_auth
def event_reminder_log_detail(request, log_id):
    """
    Detail view for a single EventReminderLog — shows per-user recipient breakdown.
    """
    log = get_object_or_404(EventReminderLog, id=log_id)
    recipients = log.recipients.select_related('user').defer(*member_defer('user')).order_by('status', 'user_name')

    dispatched = recipients.filter(status='dispatched')
    skipped = recipients.exclude(status='dispatched')

    return render(request, 'admin_v2/event_reminder_log_detail.html', {
        'log': log,
        'dispatched': dispatched,
        'skipped': skipped,
    })



@require_admin_v2_auth
def celery_health(request):
    """
    Celery task health dashboard — shows worker status and the state of every
    registered PeriodicTask (schedule, last run, run count, enabled/disabled).
    """
    from django_celery_beat.models import PeriodicTask

    now = timezone.now()

    # ── Worker ping ────────────────────────────────────────────────────────────
    # inspect().ping() broadcasts to all workers and collects responses.
    # We cap it at 1 second so a dead worker doesn't block page load.
    workers_up = False
    worker_details = []
    try:
        from celery import current_app
        inspector = current_app.control.inspect(timeout=1)
        ping_result = inspector.ping() or {}
        workers_up = bool(ping_result)
        for worker_name, response in ping_result.items():
            worker_details.append({'name': worker_name, 'response': response})
    except Exception:
        pass  # broker unreachable — workers_up stays False

    # ── Periodic tasks ─────────────────────────────────────────────────────────
    tasks = PeriodicTask.objects.select_related('interval', 'crontab').order_by('name')

    task_rows = []
    for task in tasks:
        # Build human-readable schedule string
        if task.interval:
            iv = task.interval
            schedule_str = f'Every {iv.every} {iv.period}'
        elif task.crontab:
            ct = task.crontab
            schedule_str = f'Crontab {ct.minute} {ct.hour} {ct.day_of_week} {ct.day_of_month} {ct.month_of_year}'
        else:
            schedule_str = 'Unknown schedule'

        # Staleness: interval tasks are stale if last_run_at is >2× the interval ago;
        # crontab tasks are considered stale if not run in the last 25 hours.
        stale = False
        if task.enabled and task.last_run_at:
            if task.interval:
                period_seconds = {'days': 86400, 'hours': 3600, 'minutes': 60, 'seconds': 1}
                interval_secs = task.interval.every * period_seconds.get(task.interval.period, 60)
                stale = (now - task.last_run_at).total_seconds() > interval_secs * 2
            elif task.crontab:
                stale = (now - task.last_run_at).total_seconds() > 90000  # 25 hours

        task_rows.append({
            'task': task,
            'schedule_str': schedule_str,
            'stale': stale,
        })

    return render(request, 'admin_v2/celery_health.html', {
        'workers_up': workers_up,
        'worker_details': worker_details,
        'task_rows': task_rows,
        'now': now,
    })
