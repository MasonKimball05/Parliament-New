"""
Activity logs view for officers
"""
from django.shortcuts import render
from django.core.paginator import Paginator
from src.models import ActivityLog, ParliamentUser
from ..decorators import officer_required
from django.db.models import Count, Q
from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.timezone import localtime
from src.utils.export_utils import export_to_csv
from src.models.users import member_defer
from src.kai_audit import audit_search_q, redact_kai_logs


@officer_required
def activity_logs_view(request):
    """
    View for officers to see comprehensive activity logs with filtering
    """
    # Get filter parameters
    action_category = request.GET.get('category', '')
    action_type = request.GET.get('type', '')
    user_filter = request.GET.get('user', '')
    search_query = request.GET.get('q', '')
    date_range = request.GET.get('date_range', '7')  # Default to last 7 days

    # Start with all logs
    logs = ActivityLog.objects.all().select_related('user').defer(*member_defer('user'))

    # Apply date range filter
    now = timezone.now()
    if date_range == '1':
        start_date = now - timedelta(days=1)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '7':
        start_date = now - timedelta(days=7)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '30':
        start_date = now - timedelta(days=30)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '90':
        start_date = now - timedelta(days=90)
        logs = logs.filter(timestamp__gte=start_date)
    # 'all' shows everything

    # Apply category filter
    if action_category:
        logs = logs.filter(action_category=action_category)

    # Apply action type filter
    if action_type:
        logs = logs.filter(action_type=action_type)

    # Apply user filter
    if user_filter:
        logs = logs.filter(user__user_id=user_filter)

    # Apply search filter
    #
    # ⚠️ v3.18.2 — `audit_search_q`, NOT a raw Q. Kai rows are excluded from
    # the `description` and `user__name` columns for a viewer without both
    # identity flags, because those are the two columns the page redacts and
    # **a filter predicate is a join key** — redacting the output while still
    # filtering on the input is the oracle v3.16.3 and v3.18.1 both closed
    # elsewhere. See `src/kai_audit.py`.
    if search_query:
        logs = logs.filter(audit_search_q(search_query, request.user))

    # Pagination
    paginator = Paginator(logs, 50)  # Show 50 logs per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # ⚠️ v3.18.2 — REDACT THE PAGE BEFORE IT REACHES THE TEMPLATE.
    #
    # `ActivityLog` was the eleventh Kai surface and the first that no
    # enumeration could have caught: it is not a Kai model, not in
    # `src/models/kai.py`, and not rendered by a `templates/kai/` file — it
    # just stores both party identities in a TextField called `description`
    # plus a third copy in the row's own `user` FK.
    #
    # `"<Name> submitted Kai case #12"` was written with `user=request.user`,
    # and on a submission that user IS the reporter. Every officer and chair
    # could read it here, one *Kai Committee* filter chip away.
    #
    # This mutates the page's objects in place and attaches `display_actor`,
    # `display_actor_id` and `display_description`. The template renders those.
    redact_kai_logs(page_obj.object_list, request.user)

    # Get unique users for filter dropdown
    active_users = ParliamentUser.objects.filter(
        member_status='Active'
    ).order_by('name')

    # Get statistics
    total_logs = logs.count()
    unique_users = logs.values('user').distinct().count()

    # Category counts for the filtered results.
    #
    # v3.17.3 (second pass): was one COUNT round trip per category — nine of
    # them, every load, over the same filtered queryset, and this page is
    # already scanning a date-ranged slice of the largest table in the schema.
    # One GROUP BY answers all nine. Categories with no rows are dropped by the
    # comprehension, matching the previous `if count > 0`.
    _counts = {
        row['action_category']: row['n']
        for row in logs.values('action_category').annotate(n=Count('id'))
    }
    category_counts = {
        code: {'name': name, 'count': _counts[code]}
        for code, name in ActivityLog.ACTION_CATEGORIES
        if _counts.get(code)
    }

    context = {
        'page_obj': page_obj,
        'active_users': active_users,
        'action_categories': ActivityLog.ACTION_CATEGORIES,
        'action_types': ActivityLog.ACTION_TYPES,
        'category_counts': category_counts,
        'total_logs': total_logs,
        'unique_users': unique_users,
        # Filters
        'selected_category': action_category,
        'selected_type': action_type,
        'selected_user': user_filter,
        'search_query': search_query,
        'date_range': date_range,
    }

    return render(request, 'activity_logs.html', context)


@officer_required
def export_activity_logs(request):
    """
    Export activity logs to CSV with applied filters
    """
    # Get filter parameters (same as main view)
    action_category = request.GET.get('category', '')
    action_type = request.GET.get('type', '')
    user_filter = request.GET.get('user', '')
    search_query = request.GET.get('q', '')
    date_range = request.GET.get('date_range', '7')

    # Apply same filters as the main view
    logs = ActivityLog.objects.all().select_related('user').defer(*member_defer('user'))

    # Apply date range filter
    now = timezone.now()
    if date_range == '1':
        start_date = now - timedelta(days=1)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '7':
        start_date = now - timedelta(days=7)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '30':
        start_date = now - timedelta(days=30)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '90':
        start_date = now - timedelta(days=90)
        logs = logs.filter(timestamp__gte=start_date)

    if action_category:
        logs = logs.filter(action_category=action_category)

    if action_type:
        logs = logs.filter(action_type=action_type)

    if user_filter:
        logs = logs.filter(user__user_id=user_filter)

    # v3.18.2 — same predicate as the view. The export used to duplicate the
    # raw Q, which is how `_kai_search_q`'s two call sites drifted apart in
    # v3.18.0; one helper, both callers.
    if search_query:
        logs = logs.filter(audit_search_q(search_query, request.user))

    # Prepare CSV data
    headers = [
        'Timestamp',
        'User',
        'User ID',
        'Action Category',
        'Action Type',
        'Description',
        'Object Type',
        'Object ID',
        'Object Name',
        'IP Address',
    ]

    # ⚠️ v3.18.2 — REDACTED, and this surface is the one that matters most of
    # the five: a CSV leaves the app. v3.16.2's lesson was that a redaction
    # applied to a detail page and not to its export is not a redaction; this
    # is the same pairing, so the export goes through the same helper the page
    # does rather than reading `log.description` and `log.user` raw.
    rows = []
    for log in redact_kai_logs(logs, request.user):
        rows.append([
            localtime(log.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
            log.display_actor,
            log.display_actor_id or 'N/A',
            log.get_action_category_display(),
            log.get_action_type_display(),
            log.display_description,
            log.object_type,
            log.object_id if log.object_id else '',
            log.object_repr,
            log.ip_address if log.ip_address else '',
        ])

    # Log the export
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported {len(rows)} activity log entries to CSV',
        request=request,
        metadata={'record_count': len(rows), 'filters': {
            'category': action_category,
            'type': action_type,
            'user': user_filter,
            'date_range': date_range
        }}
    )

    return export_to_csv('activity_logs', headers, rows)
