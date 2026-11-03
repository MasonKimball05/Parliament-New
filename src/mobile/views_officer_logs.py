"""
The officer log pages in the app (v4.4.3), each behind its website page's own
gate (``officer_required``: officers and chairs, not advisors) and built by the
web views' own helpers, so the app never shows more than the page does.

* ``GET officer/activity-logs/?date_range=&category=&type=&user=&q=&page=`` —
  ``activity_logs`` (/officers/activity-logs/), behind the ``activity_logs``
  feature flag as on the web. 50 rows a page, the page's filters (date range,
  category, action type, member, search) through ``filtered_activity_logs``,
  every row through ``redact_kai_logs`` (``display_*`` only: never the raw
  description, author or IP). The page's totals and per-category counts come
  with each page; the member list for the filter only with page 1.
* ``GET officer/system-logs/`` — ``view_logs`` (/officers/system-logs/): the
  last 200 lines of ``django_actions.log``, newest first, read and Kai-redacted
  by ``read_log_entries``. The web page has no filters; the app filters by level
  and text on the phone, over the same 200 lines (one bounded read, ~256 KB at
  most, so there is nothing to page).

Read only. The CSV export stays on the website.
"""
from django.core.paginator import Paginator
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.kai_audit import redact_kai_logs
from src.mobile.views_officer_tools import _Officers
from src.models import ActivityLog, ParliamentUser
from src.view.activity_logs import DATE_RANGES, activity_category_counts, filtered_activity_logs
from src.view.officer.view_logs import LOG_DISPLAY_LINES, read_log_entries

PAGE_SIZE = 50   # the web page's


def _row(log):
    # ⚠️ display_actor / display_actor_id / display_description / display_ip,
    # NEVER log.user, log.description or log.ip_address: `redact_kai_logs`
    # replaces exactly those on a Kai row this officer may not see the parties
    # of. Same rule as `templates/activity_logs.html`.
    actor = log.display_actor if log.display_actor and log.display_actor != 'System' else None
    return {
        'id': log.id,
        'timestamp': log.timestamp.isoformat(),
        'actor': actor,
        'actor_id': (log.display_actor_id or None) if actor else None,
        'category': log.action_category,
        'category_label': log.get_action_category_display(),
        'type': log.action_type,
        'type_label': log.get_action_type_display(),
        'description': log.display_description or '',
        'object': log.object_repr or '',
        'ip': log.display_ip or None,
    }


class ActivityLogsView(_Officers):
    web_page = 'activity_logs'
    web_page_toggle = False   # the page has @require_feature_flag, not a page toggle

    def get(self, request):
        if not check_feature_enabled('activity_logs'):
            raise exceptions.PermissionDenied('Activity logs are turned off.', code='feature_disabled')
        p = request.query_params
        date_range = p.get('date_range', '7')
        category = p.get('category', '')
        action_type = p.get('type', '')
        user_filter = p.get('user', '')
        q = p.get('q', '').strip()
        logs = filtered_activity_logs(
            request.user, category=category, action_type=action_type,
            user_filter=user_filter, search_query=q, date_range=date_range,
        )
        page = Paginator(logs, PAGE_SIZE).get_page(p.get('page'))
        rows = list(page.object_list)
        redact_kai_logs(rows, request.user)
        out = {
            'filters': {'date_range': date_range if date_range in DATE_RANGES else 'all',
                        'category': category, 'type': action_type, 'user': user_filter, 'q': q},
            'page': page.number,
            'pages': page.paginator.num_pages,
            'total': page.paginator.count,
            'has_next': page.has_next(),
            'unique_users': logs.values('user').distinct().count(),
            'category_counts': [{'code': code, 'label': c['name'], 'count': c['count']}
                                for code, c in activity_category_counts(logs).items()],
            'logs': [_row(log) for log in rows],
            'web_path': reverse('activity_logs'),
        }
        if page.number == 1:
            # The page's dropdowns. Members: the web's — every Active member.
            out['categories'] = [{'code': c, 'label': n} for c, n in ActivityLog.ACTION_CATEGORIES]
            out['types'] = [{'code': c, 'label': n} for c, n in ActivityLog.ACTION_TYPES]
            out['users'] = [{'user_id': u.user_id, 'name': u.get_display_name()}
                            for u in ParliamentUser.objects.filter(member_status='Active')
                            .only('user_id', 'name', 'preferred_name').order_by('name')]
        return Response(out)


class SystemLogsView(_Officers):
    web_page = 'view_logs'
    web_page_toggle = False   # view_logs has no @require_page_enabled

    def get(self, request):
        entries, state, _error = read_log_entries()
        return Response({
            # 'missing' / 'error' are the page's two banners. The exception
            # text stays in the server log, not on a phone.
            'state': state,
            'limit': LOG_DISPLAY_LINES,
            'entries': [{
                'timestamp': e['timestamp'] or None,
                'logger': e['logger'] or None,
                'level': e['level'] or None,
                # The parsed message without the "[LEVEL] " prefix, or the
                # whole (redacted) raw line when it didn't parse.
                'message': e['text'] if e['text'] is not None else e['message'],
            } for e in entries],
            'web_path': reverse('view_logs'),
        })
