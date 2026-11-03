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
from src.kai_audit import audit_search_q, exclude_kai_logs, redact_kai_logs
from src.feature_flag_decorators import require_feature_flag

#: v3.18.4 — ceiling on a single CSV export. `date_range` is a query parameter
#: and `'all'` matches every branchless case, so `/activity-logs/export/
#: ?date_range=all` asked for every row of the largest table in the schema —
#: and since v3.18.2 `redact_kai_logs` starts with `list(logs)`, so the whole
#: thing was materialised in memory before a byte was written. Same number and
#: same reasoning as `KAI_LIST_LIMIT`: a full-history dump of the audit log is
#: not something one request should be able to pull.
EXPORT_LIMIT = 5000


DATE_RANGES = ('1', '7', '30', '90', 'all')


def filtered_activity_logs(viewer, *, category='', action_type='', user_filter='',
                           search_query='', date_range='7'):
    """
    The page's queryset for these filters, as `viewer` may see it.

    v4.4.3 — one helper for the page, its CSV export and the app's Activity
    Logs screen (`src/mobile/views_officer_logs.py`), so the Kai exclusion on
    the author filter and the audit search predicate can't drift between them.
    Rows still need `redact_kai_logs` before they're shown.
    """
    logs = ActivityLog.objects.all().select_related('user').defer(*member_defer('user'))

    # Apply date range filter
    days = {'1': 1, '7': 7, '30': 30, '90': 90}.get(date_range)
    if days:
        logs = logs.filter(timestamp__gte=timezone.now() - timedelta(days=days))
    # 'all' (or anything else) shows everything

    # Apply category filter
    if category:
        logs = logs.filter(action_category=category)

    # Apply action type filter
    if action_type:
        logs = logs.filter(action_type=action_type)

    # Apply user filter
    #
    # 🔴 v3.18.4 — `exclude_kai_logs` HERE, AND IT IS NOT OPTIONAL.
    #
    # This is a filter on the row's AUTHOR, which makes it the case
    # `src/kai_audit.py` describes in its own docstring as needing exclusion
    # rather than redaction: *"the filter is on the author, so the row's
    # presence under a member's name is the leak."* `admin_v2.py`'s per-member
    # drill gets this right. This page ran the identical `filter(user=…)` and
    # went through `redact_kai_logs` alone, so:
    #
    #     /activity-logs/?user=<member>&category=kai
    #
    # returned `Anonymous submitted Kai case KAI-2026-012` — and the redaction
    # was worthless, because the officer picked the name from a dropdown listing
    # every active member. `redact_kai_logs` substitutes the identity out of the
    # description and leaves the verb and the case number, so the surviving text
    # names the action; `display_actor` reading *Anonymous* beside a filter chip
    # bearing the member's own name is a confirmation, not a redaction.
    # `officer_required` admits every officer and chair and consults no
    # `KaiMemberPermission`, so that was the whole chapter's officer corps.
    #
    # The two halves of `kai_audit` are NOT alternatives — they answer different
    # questions (*may this row be seen* vs *may it be seen under this
    # predicate*), and a surface reached by an author-valued filter needs both.
    #
    # **The rule, which is what went wrong: the PREDICATE decides which half
    # applies, not the page.** v3.18.2 classified surfaces by which view they
    # lived in and got three of four right; the fourth was the author filter on
    # the very page the module was written for. Found 08-03-26.
    if user_filter:
        logs = exclude_kai_logs(
            logs.filter(user__user_id=user_filter), viewer,
        )

    # Apply search filter
    #
    # ⚠️ v3.18.2 — `audit_search_q`, NOT a raw Q. Kai rows are excluded from
    # the `description` and `user__name` columns for a viewer without both
    # identity flags, because those are the two columns the page redacts and
    # **a filter predicate is a join key** — redacting the output while still
    # filtering on the input is the oracle v3.16.3 and v3.18.1 both closed
    # elsewhere. See `src/kai_audit.py`.
    if search_query:
        logs = logs.filter(audit_search_q(search_query, viewer))

    return logs


def activity_category_counts(logs):
    """
    `{code: {'name', 'count'}}` for the categories `logs` has rows in, in the
    model's order.

    v3.17.3 (second pass): was one COUNT round trip per category — nine of
    them, every load, over the same filtered queryset, and this page is
    already scanning a date-ranged slice of the largest table in the schema.
    One GROUP BY answers all nine. Categories with no rows are dropped by the
    comprehension, matching the previous `if count > 0`.
    """
    _counts = {
        row['action_category']: row['n']
        for row in logs.values('action_category').annotate(n=Count('id'))
    }
    category_counts = {
        code: {'name': name, 'count': _counts[code]}
        for code, name in ActivityLog.ACTION_CATEGORIES
        if _counts.get(code)
    }
    return category_counts


@officer_required
@require_feature_flag('activity_logs')
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

    # v4.4.3 — the filters live in `filtered_activity_logs`, shared with the
    # export and the app. Read its notes before changing a predicate.
    logs = filtered_activity_logs(
        request.user, category=action_category, action_type=action_type,
        user_filter=user_filter, search_query=search_query, date_range=date_range,
    )

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

    # Category counts for the filtered results (v4.4.3: a helper, shared
    # with the app).
    category_counts = activity_category_counts(logs)

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
@require_feature_flag('activity_logs')
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

    # v4.4.3 — same helper as the page (and the app), so every filter
    # combination the page offers is reachable here only as the page allows.
    # The Kai exclusion on `user` is the half that leaves the app as a file;
    # see `filtered_activity_logs`.
    logs = filtered_activity_logs(
        request.user, category=action_category, action_type=action_type,
        user_filter=user_filter, search_query=search_query, date_range=date_range,
    )

    # v3.18.4 — bounded. See `EXPORT_LIMIT`. `truncated` drives a TRAILING CSV
    # row saying so, because a silently short export is worse than a refused
    # one: the reader cannot tell a complete history from a clipped one.
    # (v3.18.5 moved the banner from first row to last and explained why forty
    # lines below, but left this comment saying "first" — so the file stated
    # both. Corrected v3.18.7. The rule it violated is this codebase's own:
    # don't leave a claim in a document nobody revisits.)
    total_matched = logs.count()
    truncated = total_matched > EXPORT_LIMIT
    logs = logs[:EXPORT_LIMIT]

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
            # ⚠️ v3.18.5 — display_ip, NOT ip_address. Same reason as the page,
            # and this is the half that leaves the app: an unredacted IP beside
            # a redacted actor in a file is the v3.16.2 lesson exactly (a
            # redaction applied to a page and not to its export is not a
            # redaction). See the note in `src/kai_audit.py`.
            log.display_ip or '',
        ])

    # v3.18.5 — the truncation notice goes LAST, not first. As row 1 it sat
    # directly under the header with prose in the Timestamp column, so anything
    # reading the file as data (`pandas.read_csv`, a spreadsheet sort, a
    # script) took it as a record with an unparseable timestamp. Last, it
    # degrades gracefully: a reader who stops early loses the warning, not the
    # parse.
    exported_count = len(rows)
    if truncated:
        rows.append([
            f'TRUNCATED — {total_matched} rows matched, showing the most recent '
            f'{EXPORT_LIMIT}. Narrow the date range or filters for the rest.',
            '', '', '', '', '', '', '', '', '',
        ])

    # Log the export
    #
    # v3.18.5 — counts the RECORDS, not `len(rows)`, which included the
    # truncation banner and was therefore off by one on exactly the exports
    # worth auditing. `total_matched` is recorded alongside it because the
    # number an auditor wants is "how much existed", not just "how much left".
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported {exported_count} activity log entries to CSV',
        request=request,
        metadata={
            'record_count': exported_count,
            'total_matched': total_matched,
            'truncated': truncated,
            'filters': {
                'category': action_category,
                'type': action_type,
                'user': user_filter,
                'date_range': date_range,
            },
        }
    )

    return export_to_csv('activity_logs', headers, rows)
