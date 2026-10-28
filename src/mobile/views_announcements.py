"""
The app's Announcements screen (mobile plan, Phase 3b).

* ``GET announcements/`` — newest first, the web page's year of posts, paged
  with ``?before=<id>&limit=`` (default 20, at most 50) and searchable with
  ``?q=`` (title or text, as the web search). ``next_before`` is the id to ask
  for the next page with, or ``null``.
* ``GET announcements/<id>/`` — one announcement (404 if the member may not
  see it, as the web list would leave it out).

Same rules as ``src/view/announcements.py``: the ``announcements`` flag and
page toggle, published and active only, ``visible_to_q`` for the audience,
and a read is recorded the way the web page records one (``view_source``
``'site'``: the app is a way of reading the site, not a new channel in the
officers' view counts). Polls and linked documents are answered on the
website, so they come with the URL to open there.
"""
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.models import Announcement, AnnouncementPollResponse, UserAnnouncementView
from src.models.users import member_defer
from src.utils.visibility import visible_to_q

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
WINDOW = timedelta(days=365)


def _visible(user):
    now = timezone.now()
    return (Announcement.objects
            .filter(is_active=True, posted_at__gte=now - WINDOW)
            .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
            .filter(visible_to_q(user.member_type))
            .select_related('posted_by', 'poll')
            .prefetch_related('linked_documents')
            .defer(*member_defer('posted_by')))


def _record_views(user, rows):
    """The web page's view tracking (announcements_view), for these rows."""
    if not rows:
        return
    seen = set(UserAnnouncementView.objects
               .filter(user=user, announcement__in=rows)
               .values_list('announcement_id', flat=True))
    Announcement.ensure_target_audience_snapshots(rows)
    missing = [UserAnnouncementView(user=user, announcement=a, view_source='site',
                                    counted_in_target=a.is_in_target_audience(user))
               for a in rows if a.pk not in seen]
    if missing:
        UserAnnouncementView.objects.bulk_create(missing, ignore_conflicts=True)


def _poll(a, responded):
    poll = getattr(a, 'poll', None)
    if poll is None:
        return None
    return {
        'title': poll.title,
        'open': poll.is_accepting_responses(),
        'responded': a.id in responded,
        'url': reverse('take_poll', args=[a.id]),
    }


def _serialize(a, responded):
    return {
        'id': a.id,
        'title': a.title,
        'content': a.content,
        'posted_at': a.posted_at.isoformat(),
        'posted_by': a.posted_by.get_display_name(),
        'event_date': a.event_date.isoformat() if a.event_date else None,
        'poll': _poll(a, responded),
        'documents': [{'id': d.id, 'title': d.title,
                       'url': reverse('view_chapter_document', args=[d.id])}
                      for d in a.linked_documents.all()],
    }


def _responded(user, rows):
    return set(AnnouncementPollResponse.objects
               .filter(respondent=user, poll__announcement__in=rows)
               .values_list('poll__announcement_id', flat=True))


class _AnnouncementsBase(MobileAPIView):
    web_page = 'announcements'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_feature_enabled('announcements'):
            raise exceptions.PermissionDenied('Announcements are off.', code='feature_disabled')


class AnnouncementListView(_AnnouncementsBase):

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get('limit', DEFAULT_LIMIT)), MAX_LIMIT))
            before = int(request.query_params['before']) if request.query_params.get('before') else None
        except ValueError:
            raise exceptions.ValidationError('limit and before are numbers.', code='invalid_request')
        qs = _visible(request.user)
        q = request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))
        if before is not None:
            # Ids follow posting order closely enough for paging, and are stable.
            qs = qs.filter(id__lt=before)
        rows = list(qs.order_by('-id')[:limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        _record_views(request.user, rows)
        responded = _responded(request.user, rows)
        return Response({
            'announcements': [_serialize(a, responded) for a in rows],
            'next_before': rows[-1].id if more else None,
        })


class AnnouncementDetailView(_AnnouncementsBase):

    def get(self, request, announcement_id):
        a = _visible(request.user).filter(pk=announcement_id).first()
        if a is None:
            raise exceptions.NotFound()
        _record_views(request.user, [a])
        return Response({'announcement': _serialize(a, _responded(request.user, [a]))})
