"""
The member's notification inbox (mobile plan, Phase 3).

* ``GET  notifications/?filter=&before=&limit=`` — newest first, own only.
  ``filter`` is ``all`` (default), ``unread`` or a notification type, as on
  the web page. Paged by id: pass the previous page's ``next_before``.
* ``GET  notifications/count/`` — the bell badge.
* ``POST notifications/<id>/read/`` — someone else's id is a 404.
* ``POST notifications/read-all/``

Reads and counts go through the same helpers as the website
(``src/view/notifications.py``, ``src/context_processors.py``), so the
announcement-view stats and the cached badge count stay in step.

Each item carries ``target`` ({url, screen, id}), the same routing a native
push uses (``src/mobile/deeplinks.py``), so tapping a notification and tapping
its push land in the same place.
"""
from django.http import Http404
from rest_framework.response import Response

from src.context_processors import unread_notification_count
from src.mobile.api import MobileAPIView
from src.mobile.deeplinks import push_data_for_url
from src.models import Notification
from src.view.notifications import mark_all_read, mark_read

DEFAULT_LIMIT = 25
MAX_LIMIT = 50


def _int_param(request, name, default=None):
    raw = request.query_params.get(name)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class _NotificationsBase(MobileAPIView):
    web_page = 'notifications'
    web_page_toggle = False   # the web page has no @require_page_enabled


class NotificationListView(_NotificationsBase):

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user)
        kind = request.query_params.get('filter', 'all')
        if kind == 'unread':
            qs = qs.filter(is_read=False)
        elif kind in dict(Notification.NOTIFICATION_TYPES):
            qs = qs.filter(notification_type=kind)

        before = _int_param(request, 'before')
        if before is not None:
            qs = qs.filter(id__lt=before)
        limit = max(1, min(_int_param(request, 'limit', DEFAULT_LIMIT), MAX_LIMIT))
        # Page by id (monotonic, and newest-first matches created_at order),
        # one row over the limit to know whether another page exists.
        rows = list(qs.order_by('-id')[:limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]

        # Committee-chat links cost a lookup each in deeplinks; a page often
        # repeats the same link, so resolve each distinct one once.
        targets = {}
        items = []
        for n in rows:
            if n.link not in targets:
                targets[n.link] = push_data_for_url(n.link)
            items.append({
                'id': n.id,
                'type': n.notification_type,
                'title': n.title,
                'message': n.message,
                'is_read': n.is_read,
                'created_at': n.created_at.isoformat(),
                'target': targets[n.link],
            })
        return Response({
            'notifications': items,
            'next_before': rows[-1].id if has_more else None,
            'unread_count': unread_notification_count(request.user),
        })


class NotificationCountView(_NotificationsBase):

    def get(self, request):
        return Response({'unread_count': unread_notification_count(request.user)})


class NotificationReadView(_NotificationsBase):

    def post(self, request, notification_id):
        notification = Notification.objects.filter(
            id=notification_id, recipient=request.user).first()
        if notification is None:
            raise Http404
        mark_read(notification, request.user)
        return Response({'ok': True, 'unread_count': unread_notification_count(request.user)})


class NotificationReadAllView(_NotificationsBase):

    def post(self, request):
        return Response({'ok': True, 'count': mark_all_read(request.user)})
