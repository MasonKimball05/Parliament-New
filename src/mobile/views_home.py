"""
The app's Home tab (mobile plan, Phase 3): ``GET home/``.

One request for the whole screen:

* ``pending_votes`` — open chapter ballots the member hasn't cast, newest
  first (up to 5). Only for members who can vote, with the Vote page open
  to them; otherwise ``null``, so the app can tell "nothing to vote on" from
  "no Votes tab". (Not the ``legislation_voting`` flag: that gates managing
  bills, not voting, on the web too; corrected v3.55.0.)
  Committee ballots arrive with the Votes endpoints.
* ``events`` — the next 3 events the member may see.
* ``announcements`` — the latest 3 published announcements they may see.
* ``unread`` — the bell count, and the chat count (``null`` when chat is off
  for them).

Every query is the web Home page's (``src/view/home.py``): the same
``open_for_voting()``, the same ``visible_to_q`` audience rule and the same
cached badge counts (``src/context_processors.py``). Each item's ``target`` is
the push routing (``src/mobile/deeplinks.py``), so a tap here and a tap on the
matching push open the same screen.
"""
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.context_processors import unread_chat_count, unread_notification_count
from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView, check_web_page
from src.mobile.deeplinks import push_data_for_url
from src.models import Announcement, Event, Legislation, Vote
from src.models.users import member_defer
from src.utils.visibility import visible_to_q

PENDING_VOTES = 5
EVENTS = 3
ANNOUNCEMENTS = 3
EXCERPT = 280


def _may_open(user, url_name, flag=None):
    """Would the website let ``user`` open ``url_name`` (behind ``flag``)?"""
    if flag and not check_feature_enabled(flag):
        return False
    try:
        check_web_page(user, url_name)
    except exceptions.PermissionDenied:
        return False
    return True


def _pending_votes(user, now):
    if not (user.can_vote and _may_open(user, 'vote')):
        return None
    voted = Vote.objects.filter(user=user).values_list('legislation_id', flat=True)
    rows = (Legislation.objects.open_for_voting(now)
            .exclude(id__in=voted)
            .only('id', 'title', 'vote_mode', 'voting_ends_at', 'available_at')
            .order_by('-available_at')[:PENDING_VOTES])
    return [{
        'id': leg.id,
        'title': leg.title,
        'vote_mode': leg.vote_mode,
        'voting_ends_at': leg.voting_ends_at.isoformat() if leg.voting_ends_at else None,
        'target': push_data_for_url(reverse('legislation_detail', args=[leg.id])),
    } for leg in rows]


def _events(now, vis):
    rows = (Event.objects
            .filter(is_active=True, archived=False, date_time__gte=now)
            .filter(vis)
            .only('id', 'title', 'date_time', 'location')
            .order_by('date_time')[:EVENTS])
    calendar = push_data_for_url(reverse('calendar'))
    return [{
        'id': e.id,
        'title': e.title,
        'date_time': e.date_time.isoformat(),
        'location': e.location,
        'target': {**calendar, 'id': e.id},
    } for e in rows]


def _announcements(now, vis):
    rows = (Announcement.objects
            .filter(is_active=True)
            .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
            .filter(vis)
            .select_related('posted_by').defer(*member_defer('posted_by'))
            .order_by('-posted_at')[:ANNOUNCEMENTS])
    target = push_data_for_url(reverse('announcements'))
    return [{
        'id': a.id,
        'title': a.title,
        'excerpt': a.content[:EXCERPT],
        'posted_at': a.posted_at.isoformat(),
        'posted_by': a.posted_by.get_display_name(),
        'target': target,
    } for a in rows]


class HomeView(MobileAPIView):
    web_page = 'home'

    def get(self, request):
        user = request.user
        now = timezone.now()
        vis = visible_to_q(user.member_type)
        return Response({
            'pending_votes': _pending_votes(user, now),
            'events': _events(now, vis),
            'announcements': _announcements(now, vis),
            'unread': {
                'notifications': unread_notification_count(user),
                'chat': (unread_chat_count(user)
                         if _may_open(user, 'chat_index', 'chats') else None),
            },
        })
