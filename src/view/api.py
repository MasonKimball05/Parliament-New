"""
API endpoints for Parliament system.

Includes:
  - dismiss_announcement_api — dismiss an announcement notification
  - request_api_token        — request a new APIToken (pending or auto-approved)
  - revoke_api_token         — revoke the user's own token
  - Admin views (require request.user.is_admin):
      admin_api_tokens           — list all tokens with status filter
      admin_approve_token        — approve a pending token
      admin_reject_token         — reject a pending token
      admin_revoke_token         — revoke an active token
      admin_update_token_scopes  — edit the scopes on any token
      admin_api_token_logs       — view access logs for a specific token
"""
from datetime import date as _date

from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.utils import timezone

from src.notifications import mark_announcement_dismissed
from src.models.api import APIToken, APIAccessLog, DEFINED_SCOPES, ALL_SCOPE_KEYS
from src.models_feature_flags import FeatureFlag
from src.models import ActivityLog
from src.models.admin_audit import log_admin_action

import logging
from src.models.users import member_defer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

@login_required
@require_POST
def dismiss_announcement_api(request, announcement_id):
    """Mark an announcement as dismissed for the current user."""
    try:
        success = mark_announcement_dismissed(request.user, announcement_id)
        if success:
            return JsonResponse({'success': True})
        return JsonResponse({'success': False, 'error': 'Failed to dismiss announcement'}, status=500)
    except Exception as e:
        logger.error(f"Error in dismiss_announcement_api: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_expiry(raw):
    """Parse a 'YYYY-MM-DD' expiry string.

    Returns:
        (aware_datetime, None)   — valid future date
        (None, None)             — raw is empty/blank (no expiry requested)
        (None, JsonResponse)     — validation error; caller should return the response
    """
    if not raw:
        return None, None
    try:
        parsed = _date.fromisoformat(raw)
        if parsed <= _date.today():
            return None, JsonResponse({'error': 'Expiry date must be in the future.'}, status=400)
        return timezone.make_aware(
            timezone.datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59)
        ), None
    except ValueError:
        return None, JsonResponse({'error': 'Invalid expiry date.'}, status=400)


# ---------------------------------------------------------------------------
# User-facing token management
# ---------------------------------------------------------------------------

@require_http_methods(["POST"])
@login_required
def request_api_token(request):
    """
    Request a new API token.

    Creates as 'active' immediately if the api_token_auto_approve feature flag
    is enabled, otherwise creates as 'pending' and waits for admin approval.

    Returns 400 if the user already has a non-revoked, non-rejected token.
    """
    # Block if the user already has a live (pending or active) token
    existing = APIToken.objects.filter(
        user=request.user,
    ).exclude(status__in=[APIToken.STATUS_REVOKED, APIToken.STATUS_REJECTED]).first()
    if existing:
        return JsonResponse(
            {'error': 'You already have an active or pending token.'},
            status=400,
        )

    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Token name is required.'}, status=400)

    scopes_raw = request.POST.getlist('scopes')
    valid_scopes = [s for s in scopes_raw if s in ALL_SCOPE_KEYS]
    if not valid_scopes:
        return JsonResponse({'error': 'At least one valid scope is required.'}, status=400)

    request_note = request.POST.get('request_note', '').strip()

    expires_at, expiry_error = _parse_expiry(request.POST.get('expires_at', '').strip())
    if expiry_error:
        return expiry_error

    auto_approve = FeatureFlag.is_feature_enabled('api_token_auto_approve')

    token = APIToken(
        user=request.user,
        key=APIToken.generate_key(),
        name=name,
        scopes=valid_scopes,
        request_note=request_note,
        expires_at=expires_at,
        status=APIToken.STATUS_ACTIVE if auto_approve else APIToken.STATUS_PENDING,
    )
    if auto_approve:
        token.approved_at = timezone.now()
    token.save()

    return JsonResponse({
        'status': token.status,
        'pending': token.status == APIToken.STATUS_PENDING,
        'message': (
            'Token created and active.'
            if auto_approve
            else 'Token request submitted for approval.'
        ),
    })


@require_http_methods(["POST"])
@login_required
def revoke_api_token(request):
    """Revoke the user's own API token by ID."""
    token_id = request.POST.get('token_id')
    if not token_id:
        return JsonResponse({'error': 'token_id is required.'}, status=400)
    try:
        token = APIToken.objects.get(id=token_id, user=request.user)
    except APIToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found.'}, status=404)
    if token.status == APIToken.STATUS_REVOKED:
        return JsonResponse({'error': 'Token is already revoked.'}, status=400)
    token.status = APIToken.STATUS_REVOKED
    token.revoked_by = request.user
    token.revoked_at = timezone.now()
    token.revoke_reason = 'Revoked by owner'
    token.save(update_fields=['status', 'revoked_by', 'revoked_at', 'revoke_reason'])
    return JsonResponse({'revoked': True})


# ---------------------------------------------------------------------------
# Admin token management
# ---------------------------------------------------------------------------

def _require_admin(request):
    """Return HttpResponseForbidden if the user is not an admin, else None."""
    if not request.user.is_admin:
        return HttpResponseForbidden('Admin access required.')
    return None


@login_required
def admin_api_tokens(request):
    """Admin view: list all API tokens with optional status filter."""
    forbidden = _require_admin(request)
    if forbidden:
        return forbidden

    status_filter = request.GET.get('status', 'all')
    tokens = (
        APIToken.objects
        .select_related('user', 'approved_by', 'revoked_by').defer(*member_defer('user', 'approved_by', 'revoked_by'))
        .order_by('-created_at')
    )
    if status_filter != 'all':
        tokens = tokens.filter(status=status_filter)

    STATUS_CHOICES = [
        ('all', 'All'),
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('revoked', 'Revoked'),
        ('rejected', 'Rejected'),
    ]

    # Load the two API-related feature flags for the settings panel.
    #
    # v3.19.7 — one query, was two `objects.get()`s.
    #
    # ⚠️ AND THE EXEMPTION THAT COVERED THEM DESCRIBED SOMETHING ELSE. This page
    # was in `test_url_smoke.ACCEPTED_REPEATS` as *"reads three different flags
    # through the cached `FeatureFlag.is_feature_enabled`; the repeats are cache
    # misses on a cold cache"* — which was the reason a reviewer would accept.
    # Two of the three were these, and they do not go through
    # `is_feature_enabled` at all: they are raw `.get()`s that read the row
    # object (the panel renders the toggle state and the display name), so they
    # bypass the v3.17.1 cache and repeat on a WARM cache, on every request,
    # forever. The exemption was accurate about the count and wrong about the
    # cause, and being wrong about the cause is what made it look temporary.
    #
    # These two must stay row reads — the template needs the objects — so the
    # fix is to fetch them together rather than to cache them.
    _api_flags = {
        flag.name: flag
        for flag in FeatureFlag.objects.filter(
            name__in=('rest_api', 'api_token_auto_approve'))
    }
    flag_rest_api = _api_flags.get('rest_api')
    flag_auto_approve = _api_flags.get('api_token_auto_approve')

    context = {
        'tokens': tokens,
        'status_filter': status_filter,
        'status_choices': STATUS_CHOICES,
        'defined_scopes': DEFINED_SCOPES,
        'flag_rest_api': flag_rest_api,
        'flag_auto_approve': flag_auto_approve,
        'now': timezone.now(),
    }
    return render(request, 'admin_v2/api_tokens.html', context)


@require_http_methods(["POST"])
@login_required
def admin_toggle_api_flag(request, flag_name):
    """Toggle one of the two API feature flags directly from the API tokens page."""
    forbidden = _require_admin(request)
    if forbidden:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if flag_name not in ('rest_api', 'api_token_auto_approve'):
        return JsonResponse({'error': 'Invalid flag.'}, status=400)
    try:
        flag = FeatureFlag.objects.get(name=flag_name)
    except FeatureFlag.DoesNotExist:
        return JsonResponse({'error': 'Flag not found.'}, status=404)
    flag.is_enabled = not flag.is_enabled
    flag.last_toggled_by = request.user.get_display_name()
    flag.last_toggled_at = timezone.now()
    flag.save(update_fields=['is_enabled', 'last_toggled_by', 'last_toggled_at'])
    return JsonResponse({'is_enabled': flag.is_enabled})


@require_http_methods(["POST"])
@login_required
def admin_approve_token(request, token_id):
    """Admin: approve a pending token, optionally setting an expiry date."""
    forbidden = _require_admin(request)
    if forbidden:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        token = APIToken.objects.get(id=token_id, status=APIToken.STATUS_PENDING)
    except APIToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found or not pending.'}, status=404)

    expires_at_raw = request.POST.get('expires_at', '').strip()
    expires_at, expiry_error = _parse_expiry(expires_at_raw)
    if expiry_error:
        return expiry_error

    # Scope narrowing: admin can uncheck scopes to grant less than what was requested.
    # scopes_submitted marker distinguishes "admin explicitly sent empty list" from "old client sent nothing".
    if 'scopes_submitted' in request.POST:
        valid = set(ALL_SCOPE_KEYS)
        approved_scopes = [s for s in request.POST.getlist('scopes') if s in valid]
        token.scopes = approved_scopes

    token.status = APIToken.STATUS_ACTIVE
    token.approved_by = request.user
    token.approved_at = timezone.now()
    if expires_at_raw:
        token.expires_at = expires_at
        token.save(update_fields=['status', 'approved_by', 'approved_at', 'expires_at', 'scopes'])
    else:
        token.save(update_fields=['status', 'approved_by', 'approved_at', 'scopes'])
    log_admin_action(
        actor=request.user, action='token_approved', request=request,
        target_user=token.user, target_repr=token.name,
        detail=f"Scopes: {', '.join(token.scopes) or '(none)'}; expires: {token.expires_at or 'never'}",
    )
    return JsonResponse({'approved': True})


@require_http_methods(["POST"])
@login_required
def admin_reject_token(request, token_id):
    """Admin: reject a pending token."""
    forbidden = _require_admin(request)
    if forbidden:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    reason = request.POST.get('reason', '').strip()
    try:
        token = APIToken.objects.get(id=token_id, status=APIToken.STATUS_PENDING)
    except APIToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found or not pending.'}, status=404)
    token.status = APIToken.STATUS_REJECTED
    token.rejection_reason = reason
    token.save(update_fields=['status', 'rejection_reason'])
    log_admin_action(
        actor=request.user, action='token_denied', request=request,
        target_user=token.user, target_repr=token.name,
        detail=f"Reason: {reason or '(none given)'}",
    )
    return JsonResponse({'rejected': True})


@require_http_methods(["POST"])
@login_required
def admin_revoke_token(request, token_id):
    """Admin: revoke an active token."""
    forbidden = _require_admin(request)
    if forbidden:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    reason = request.POST.get('reason', '').strip()
    try:
        token = APIToken.objects.get(id=token_id, status=APIToken.STATUS_ACTIVE)
    except APIToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found or not active.'}, status=404)
    token.status = APIToken.STATUS_REVOKED
    token.revoked_by = request.user
    token.revoked_at = timezone.now()
    token.revoke_reason = reason
    token.save(update_fields=['status', 'revoked_by', 'revoked_at', 'revoke_reason'])
    log_admin_action(
        actor=request.user, action='token_revoked', request=request,
        target_user=token.user, target_repr=token.name,
        detail=f"Reason: {reason or '(none given)'}",
    )
    return JsonResponse({'revoked': True})


@require_http_methods(["POST"])
@login_required
def admin_update_token_scopes(request, token_id):
    """Admin: update which scopes a token is allowed."""
    forbidden = _require_admin(request)
    if forbidden:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        token = APIToken.objects.get(id=token_id)
    except APIToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found.'}, status=404)
    old_scopes = list(token.scopes)
    scopes_raw = request.POST.getlist('scopes')
    valid_scopes = [s for s in scopes_raw if s in ALL_SCOPE_KEYS]
    token.scopes = valid_scopes
    token.save(update_fields=['scopes'])
    log_admin_action(
        actor=request.user, action='token_scopes_edited', request=request,
        target_user=token.user, target_repr=token.name,
        detail=f"Before: {', '.join(old_scopes) or '(none)'}; After: {', '.join(valid_scopes) or '(none)'}",
    )
    return JsonResponse({'scopes': token.scopes})


@login_required
def admin_api_token_logs(request, token_id):
    """Admin: view access logs for a specific token."""
    forbidden = _require_admin(request)
    if forbidden:
        return forbidden
    try:
        token = APIToken.objects.select_related('user').defer(*member_defer('user')).get(id=token_id)
    except APIToken.DoesNotExist:
        raise Http404
    logs = APIAccessLog.objects.filter(token=token).order_by('-timestamp')[:200]
    context = {
        'token': token,
        'logs': logs,
        'defined_scopes': DEFINED_SCOPES,
    }
    return render(request, 'admin_v2/api_token_logs.html', context)
