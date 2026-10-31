"""
The app's Home tab (mobile plan, Phase 3): ``GET home/``.

One request for the whole screen:

* ``pending_votes`` — open chapter ballots the member hasn't cast, newest
  first (up to 5). Only for members who can vote, with the Vote page open
  to them; otherwise ``null``, so the app can tell "nothing to vote on" from
  "no Votes tab". (Not the ``legislation_voting`` flag: that gates managing
  bills, not voting, on the web too; corrected v4.0.0.)
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
from django.core.cache import cache
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
        'target': {**target, 'id': a.id},   # the app opens that announcement
    } for a in rows]


# v4.4.0: the website Home's Quick Links (templates/home.html), for the app's
# Home. Each: (key, label, url name, page-toggle flag or None, icon, tone).
# Icons are SF Symbol / Material names. Directory and Documents are left out:
# the app's Home has them as its big shortcuts. The admin site is too: it's
# no use on a phone.
QUICK_LINKS = (
    ('vote', 'Vote', 'vote', None, ('checkmark.square', 'how_to_vote'), 'accent'),
    ('committees', 'Committees', 'committee_index', None, ('person.3', 'groups'), None),
    ('chat', 'Chat', 'chat_index', 'chats', ('bubble.left.and.bubble.right', 'forum'), None),
    ('excuses', 'Excuses', 'my_excuses', None, ('calendar.badge.minus', 'event_busy'), None),
    ('constitution', 'Constitution', 'constitution_bylaws', None, ('book.closed', 'menu_book'), None),
    ('roberts', 'Robert’s Rules', 'roberts_rules', None, ('hammer', 'gavel'), None),
    ('legislation', 'Legislation', 'view_legislation_history', None, ('clock.arrow.circlepath', 'history'), None),
    ('songbook', 'Songbook', 'songbook', None, ('music.note', 'music_note'), None),
    ('profile', 'Profile', 'profile', None, ('person.crop.circle', 'account_circle'), None),
    ('preferences', 'Preferences', 'preferences', None, ('slider.horizontal.3', 'tune'), None),
    ('groupme', 'GroupMe', 'groupme_dashboard', None, ('message', 'chat'), None),
    ('bug', 'Report a bug', 'bug_report', None, ('ladybug', 'bug_report'), 'warning'),
    ('feedback', 'Feedback', 'feedback_request', None, ('lightbulb', 'lightbulb'), 'warning'),
    ('slating', 'Slating', 'slating_dashboard', None, ('list.bullet.clipboard', 'checklist'), 'pro'),
)


def _quick_links(user, pending_votes):
    """The links ``user`` would see on the website's Home, in its order."""
    from src.models import SlatingPeriod
    out = []
    for key, label, url_name, flag, (ios, android), tone in QUICK_LINKS:
        if key == 'groupme' and not user.can_manage_groupme:
            continue
        # The website shows Slating while a period is active, to everyone but
        # pledges. Cached for a minute: Home is opened constantly, and its
        # query count is kept flat (HomeTests.test_query_count_is_flat).
        if key == 'slating' and (user.is_pledge or not cache.get_or_set(
                'mobile:home:slating_active', lambda: SlatingPeriod.objects.filter(
                    status__in=['nominations_open', 'voting_open', 'results_published']).exists(), 60)):
            continue
        if not _may_open(user, url_name, flag):
            continue
        out.append({
            'key': key, 'label': label, 'path': reverse(url_name),
            'icon': {'ios': ios, 'android': android}, 'tone': tone,
            'badge': len(pending_votes or []) if key == 'vote' else 0,
        })
    return out


class HomeView(MobileAPIView):
    web_page = 'home'

    def get(self, request):
        user = request.user
        now = timezone.now()
        vis = visible_to_q(user.member_type)
        pending_votes = _pending_votes(user, now)
        return Response({
            'pending_votes': pending_votes,
            'quick_links': _quick_links(user, pending_votes),
            'events': _events(now, vis),
            'announcements': _announcements(now, vis),
            'unread': {
                'notifications': unread_notification_count(user),
                'chat': (unread_chat_count(user)
                         if _may_open(user, 'chat_index', 'chats') else None),
            },
        })
