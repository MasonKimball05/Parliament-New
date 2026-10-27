"""
Admin v2: what admins did (v3.58.1).

The full audit log mixes every member's sign-ins, votes and page views with
the handful of rows that matter when you ask "who changed this?". This feed is
the activity of admins (the admin-v2 allowlist and anyone with is_admin) minus
routine member actions, filterable by admin and action. Kai rows are left out
the same way as elsewhere (src/kai_audit.py).
"""
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from src.kai_audit import exclude_kai_logs
from src.models import ActivityLog, ParliamentUser
from src.view import admin_v2
from src.view.admin_v2 import require_admin_v2_auth

# Things every member does; not admin actions even when an admin does them.
ROUTINE = (
    'login', 'logout', 'login_failed', 'password_changed', 'password_reset', 'vote_cast', 'vote_changed',
    'document_viewed', 'document_downloaded', 'preferences_updated', 'profile_updated',
    'profile_picture_changed', 'pledge_login', 'pledge_password_changed', 'bug_report_submitted',
    'quote_submitted', 'view',
)
PAGE_SIZE = 50


def admin_actors():
    return ParliamentUser.objects.filter(Q(is_admin=True) | Q(user_id__in=admin_v2.ALLOWED_USER_IDS))


def admin_activity(viewer, actor='', action='', q=''):
    # Kai rows out first; every filter below narrows an already-safe queryset.
    qs = exclude_kai_logs(
        ActivityLog.objects.filter(user__in=admin_actors()).exclude(action_type__in=ROUTINE), viewer,
    ).select_related('user').order_by('-timestamp')
    if actor:
        qs = exclude_kai_logs(qs.filter(user__user_id=actor), viewer)
    if action:
        qs = qs.filter(action_type=action)
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(object_repr__icontains=q))
    return qs


@require_admin_v2_auth
def admin_v2_admin_activity(request):
    actor = request.GET.get('actor', '')
    action = request.GET.get('action', '')
    q = (request.GET.get('q') or '').strip()[:100]
    page = Paginator(admin_activity(request.user, actor, action, q), PAGE_SIZE).get_page(request.GET.get('page'))
    actions = (ActivityLog.objects.filter(user__in=admin_actors()).exclude(action_type__in=ROUTINE)
               .values_list('action_type', flat=True).distinct().order_by('action_type'))
    return render(request, 'admin_v2/admin_activity.html', {
        'page': page, 'actor': actor, 'action': action, 'q': q,
        'actors': admin_actors().order_by('name').only('user_id', 'name', 'preferred_name'),
        'actions': list(actions),
    })
