"""
Admin v2: push composer (v4.4.4).

Send one push notification to a group: everyone, officers, a committee, a
member type, a member status, or one member. Two steps, both POST:

1. ``admin_v2_push`` (POST ``preview``) checks the form, works out who would
   get it and shows the notification, the recipient count and a signed
   confirm token. Nothing is sent.
2. ``admin_v2_push_send`` (POST ``token``) re-checks the token (signed, this
   admin's, at most 15 minutes old, usable once), works the audience out again
   and queues the sends.

Delivery is the same path every other push takes: ``tasks.send_push_notification``
per member (Celery), which fans out to the member's browsers (Web Push) and
their signed-in app installs (Expo, ``live_push_devices``). Who is counted:

* Active-account members only: not Removed, ``is_active``, no pending account
  deletion (``audience_members``);
* who left "Push: Announcements" on (``UserPreferences.push_announcements``,
  the per-type toggle an admin broadcast falls under; no preferences row
  means on, as everywhere else);
* who can receive a push at all: a browser subscription or a live app device
  with a push token (the event reminders' "subscribed" rule);
* and nothing at all while the "Push Notifications (Master)" flag is off.

Each send writes one ActivityLog row (``object_type='AdminPush'``, with the
audience, count (``n``), title and link in ``metadata``); the page's "Recent sends"
list reads those rows, so no new table was needed. Sends are limited to
``RATE_LIMIT`` per admin per hour (cache counter).
"""
import uuid

from django.contrib import messages
from django.core import signing
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve
from django.views.decorators.http import require_POST
from urllib.parse import urlsplit

from src.models import ActivityLog, Committee, ParliamentUser, PushSubscription
from src.models_feature_flags import FeatureFlag
from src.view.admin_v2 import require_admin_v2_auth

TITLE_MAX = 80
BODY_MAX = 300
LINK_MAX = 300
TOKEN_SALT = 'admin_v2_push'
TOKEN_MAX_AGE = 15 * 60
RATE_LIMIT = 5            # sends per admin ...
RATE_WINDOW = 60 * 60     # ... per hour
LOG_OBJECT_TYPE = 'AdminPush'
PREF_ATTR = 'push_announcements'

AUDIENCES = (
    ('everyone', 'Everyone (Active members)'),
    ('officers', 'Officers'),
    ('committee', 'A committee'),
    ('type', 'A member type'),
    ('status', 'A member status'),
    ('member', 'One member'),
)
MEMBER_TYPES = tuple(t for t, _ in ParliamentUser.MEMBER_TYPES)
STATUSES = ('Active', 'Inactive', 'Alumni')


class PushFormError(ValueError):
    pass


# ---------------------------------------------------------------- audience

def _reachable_members():
    """Members a push may go to at all, whatever the audience."""
    return (ParliamentUser.objects.exclude(member_status='Removed')
            .filter(is_active=True, deletion_requested_at__isnull=True))


def parse_audience(data):
    """{'kind', 'value'} from the form, or PushFormError."""
    kind = (data.get('audience') or '').strip()
    if kind not in dict(AUDIENCES):
        raise PushFormError('Choose who the push goes to.')
    value = ''
    if kind == 'committee':
        value = (data.get('committee') or '').strip()
        if not value.isdigit() or not Committee.objects.filter(pk=int(value)).exists():
            raise PushFormError('Choose a committee.')
    elif kind == 'type':
        value = (data.get('member_type') or '').strip()
        if value not in MEMBER_TYPES:
            raise PushFormError('Choose a member type.')
    elif kind == 'status':
        value = (data.get('member_status') or '').strip()
        if value not in STATUSES:
            raise PushFormError('Choose a member status.')
    elif kind == 'member':
        value = (data.get('user_id') or '').strip()
        if not value or not _reachable_members().filter(user_id=value).exists():
            raise PushFormError('Choose a member.')
    return {'kind': kind, 'value': value}


def audience_members(audience):
    """The queryset of members an audience names (before preferences and devices)."""
    kind, value = audience['kind'], audience['value']
    qs = _reachable_members()
    if kind == 'everyone':
        qs = qs.filter(member_status='Active')
    elif kind == 'officers':
        qs = qs.filter(member_status='Active').filter(Q(member_type='Officer') | Q(roles__isnull=False))
    elif kind == 'committee':
        committee = int(value)
        qs = qs.filter(member_status='Active').filter(
            Q(committees__pk=committee) | Q(chair_roles__pk=committee))
    elif kind == 'type':
        qs = qs.filter(member_status='Active', member_type=value)
    elif kind == 'status':
        qs = qs.filter(member_status=value)
    elif kind == 'member':
        qs = qs.filter(user_id=value)
    else:
        return ParliamentUser.objects.none()
    return qs.distinct()


def audience_label(audience):
    kind, value = audience['kind'], audience['value']
    if kind == 'committee':
        c = Committee.objects.filter(pk=int(value)).only('name').first()
        return f'the {c.name.strip()} committee' if c else 'a committee'
    if kind == 'type':
        return f'{value} members' if value != 'Member' else 'Members'
    if kind == 'status':
        return f'{value} members'
    if kind == 'member':
        m = ParliamentUser.objects.filter(user_id=value).only('name', 'preferred_name').first()
        return m.get_display_name() if m else 'one member'
    return 'everyone' if kind == 'everyone' else 'officers'


def resolve_recipients(audience):
    """
    Who in ``audience`` would get the push. Returns a dict:
    ``members`` (in the audience), ``opted_out`` (Push: Announcements off),
    ``no_device`` (nothing to push to), ``recipients`` (the rest, sorted by name).
    """
    from src.mobile.push import live_push_devices

    members = list(audience_members(audience).select_related('preferences').order_by('name'))
    wanted, opted_out = [], []
    for m in members:
        try:
            on = getattr(m.preferences, PREF_ATTR, True)
        except Exception:  # no preferences row: defaults on
            on = True
        (wanted if on else opted_out).append(m)
    ids = [m.pk for m in wanted]
    reachable = set(PushSubscription.objects.filter(user_id__in=ids).values_list('user_id', flat=True))
    reachable |= {d.user_id for d in live_push_devices(ids)}
    recipients = [m for m in wanted if m.pk in reachable]
    no_device = [m for m in wanted if m.pk not in reachable]
    return {'members': members, 'opted_out': opted_out, 'no_device': no_device, 'recipients': recipients}


# ---------------------------------------------------------------- message

def clean_link(raw):
    """'' or a same-site relative path that resolves to a page on this site."""
    link = (raw or '').strip()
    if not link:
        return ''
    if len(link) > LINK_MAX:
        raise PushFormError('The link is too long.')
    if (not link.startswith('/') or link.startswith('//') or '\\' in link
            or any(ord(c) < 0x21 or ord(c) == 0x7f for c in link)):
        raise PushFormError('The link must be a page on this site, starting with / (for example /calendar/).')
    parts = urlsplit(link)
    if parts.scheme or parts.netloc:
        raise PushFormError('The link must be a page on this site, starting with / (for example /calendar/).')
    try:
        resolve(parts.path)
    except Resolver404:
        raise PushFormError(f'{parts.path} is not a page on this site.')
    return link


def parse_message(data):
    title = ' '.join((data.get('title') or '').split())
    body = (data.get('body') or '').strip()
    if not title:
        raise PushFormError('Give the push a title.')
    if len(title) > TITLE_MAX:
        raise PushFormError(f'Keep the title to {TITLE_MAX} characters.')
    if not body:
        raise PushFormError('Write the message.')
    if len(body) > BODY_MAX:
        raise PushFormError(f'Keep the message to {BODY_MAX} characters.')
    return title, body, clean_link(data.get('link'))


# ---------------------------------------------------------------- limits and log

def _rate_key(user):
    return f'admin_v2_push_sends_{user.pk}'


def sends_left(user):
    return max(0, RATE_LIMIT - (cache.get(_rate_key(user)) or 0))


def _count_send(user):
    key = _rate_key(user)
    if not cache.add(key, 1, RATE_WINDOW):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, RATE_WINDOW)


def recent_sends(limit=15):
    return list(ActivityLog.objects.filter(object_type=LOG_OBJECT_TYPE)
                .select_related('user').order_by('-timestamp')[:limit])


def push_master_on():
    return FeatureFlag.is_feature_enabled('push_notifications_enabled')


def send_admin_push(request, audience, title, body, link):
    """Queue the push for everyone ``resolve_recipients`` picks; log it. Returns the count."""
    from src.tasks import send_push_notification

    found = resolve_recipients(audience)
    recipients = found['recipients']
    if not recipients:
        return 0
    url = link or '/notifications/'
    tag = f'admin-push-{uuid.uuid4().hex[:8]}'
    for m in recipients:
        send_push_notification.delay(user_id=m.pk, title=title, body=body, url=url, tag=tag)
    _count_send(request.user)
    label = audience_label(audience)
    ActivityLog.log_activity(
        action_type='other', user=request.user,
        description=f'{request.user.get_display_name()} sent a push to {label} '
                    f'({len(recipients)} member(s)): "{title}"',
        object_type=LOG_OBJECT_TYPE, object_repr=title[:500],
        metadata={'audience': audience, 'audience_label': label, 'n': len(recipients),
                  'opted_out': len(found['opted_out']), 'no_device': len(found['no_device']),
                  'title': title, 'body': body, 'link': link},
        request=request)
    return len(recipients)


def push_preview(user, data):
    """
    Check the composer form and work out who would get it: the preview dict
    (with ``recipient_names``, the first 50, and the signed confirm ``token``).
    Sends nothing. Raises PushFormError. Shared by the page and the app.
    """
    audience = parse_audience(data)
    title, body, link = parse_message(data)
    found = resolve_recipients(audience)
    token = signing.dumps({'a': audience, 't': title, 'b': body, 'l': link,
                           'u': user.pk, 'n': uuid.uuid4().hex}, salt=TOKEN_SALT)
    return {
        'audience_label': audience_label(audience), 'title': title, 'body': body, 'link': link,
        'members': len(found['members']), 'opted_out': len(found['opted_out']),
        'no_device': len(found['no_device']), 'n': len(found['recipients']),
        'names': [m.get_display_name() for m in found['recipients'][:50]],
        'token': token,
    }


def send_previewed_push(request, token):
    """
    Send a previewed push from its confirm token (signed, this admin's, at
    most 15 minutes old, used once), within the master switch and the hourly
    limit: (level, message, n sent). level is 'success', 'warning' or 'error'.
    Shared by the page and the app.
    """
    try:
        data = signing.loads(token or '', salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        return 'error', 'That preview is more than 15 minutes old. Preview it again.', 0
    except signing.BadSignature:
        return 'error', 'Preview the push first, then confirm it.', 0
    if not isinstance(data, dict) or data.get('u') != request.user.pk:
        return 'error', 'Preview the push first, then confirm it.', 0
    if not push_master_on():
        return ('error', 'Push notifications are switched off site-wide (Maintenance, '
                         '"Push Notifications (Master)"). Nothing was sent.', 0)
    if sends_left(request.user) <= 0:
        return 'error', f'You can send {RATE_LIMIT} pushes an hour. Try again later.', 0
    # One token, one send: a double click or a back-button resubmit does nothing.
    if not cache.add(f'admin_v2_push_token_{data.get("n")}', 1, TOKEN_MAX_AGE + 60):
        return 'warning', 'That push was already sent.', 0
    try:
        audience = parse_audience({'audience': data['a']['kind'], 'committee': data['a']['value'],
                                   'member_type': data['a']['value'], 'member_status': data['a']['value'],
                                   'user_id': data['a']['value']})
        title, body, link = parse_message({'title': data['t'], 'body': data['b'], 'link': data['l']})
    except (PushFormError, KeyError, TypeError) as exc:
        return ('error', f'{exc} Nothing was sent.' if isinstance(exc, PushFormError)
                else 'Preview the push first, then confirm it.', 0)
    n = send_admin_push(request, audience, title, body, link)
    if n:
        return 'success', f'Push queued for {n} member(s). It should arrive within a minute.', n
    return 'warning', 'Nobody in that group can get a push right now, so nothing was sent.', 0


# ---------------------------------------------------------------- views

def _context(request, form=None, preview=None, error=None):
    return {
        'audiences': AUDIENCES,
        'member_types': MEMBER_TYPES,
        'statuses': STATUSES,
        'committees': Committee.objects.filter(is_active=True, is_archived=False).order_by('name').only('pk', 'name'),
        'members': _reachable_members().order_by('name').only('user_id', 'name', 'preferred_name'),
        'form': form or {'audience': 'everyone'},
        'preview': preview,
        'error': error,
        'recent': recent_sends(),
        'sends_left': sends_left(request.user),
        'rate_limit': RATE_LIMIT,
        'title_max': TITLE_MAX,
        'body_max': BODY_MAX,
        'master_on': push_master_on(),
    }


@require_admin_v2_auth
def admin_v2_push(request):
    """GET: the composer. POST: check the form and show the preview (sends nothing)."""
    if request.method != 'POST':
        return render(request, 'admin_v2/push_composer.html', _context(request))
    form = {k: (request.POST.get(k) or '') for k in
            ('audience', 'committee', 'member_type', 'member_status', 'user_id', 'title', 'body', 'link')}
    try:
        preview = push_preview(request.user, request.POST)
    except PushFormError as exc:
        return render(request, 'admin_v2/push_composer.html', _context(request, form, error=str(exc)), status=400)
    return render(request, 'admin_v2/push_composer.html', _context(request, form, preview=preview))


@require_admin_v2_auth
@require_POST
def admin_v2_push_send(request):
    """Send a previewed push. Only with a valid, unused confirm token from this admin."""
    level, message, _n = send_previewed_push(request, request.POST.get('token', ''))
    getattr(messages, level)(request, message)
    return redirect('admin_v2_push')
