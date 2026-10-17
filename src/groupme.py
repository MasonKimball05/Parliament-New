"""
v3.49.0 — GroupMe bot: the API client, the "which groups may get this" rule,
the message format, and the dispatcher. Models are in src/models/groupme.py,
the officer dashboard in src/view/officer/groupme.py, the Celery tasks in
src/tasks/groupme.py.

THE AUDIENCE RULE (Mason, 10-17-26: one GroupMe with only brothers, one with
brothers and pledges; "it sends to [whichever] depends on the perms"):

    An announcement may be posted to a channel only if EVERYONE in that GroupMe
    is allowed to see it on Parliament, i.e. channel.audience ⊆ visible_to.
    "Visible to everyone" (empty visible_to) allows every channel.

So a members-only announcement can go to the brothers GroupMe but not the one
with pledges in it, and a pledges-only announcement goes nowhere (no GroupMe
is pledges-only). It is checked on the form AND again when it is sent, because
"Visible to" can change between the two.

The form pre-ticks the widest allowed channels (`default_channels`): for an
everyone announcement that's "Brothers + Pledges" only, because the brothers
are in that group too and would otherwise get it twice.
"""
import logging

import requests
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger('src')

API_BASE = 'https://api.groupme.com/v3'
TIMEOUT_SECONDS = 10

#: GroupMe's message length limit. Messages are built to fit, never split, so
#: the group sees one post per announcement.
MAX_MESSAGE_CHARS = 1000


class GroupMeError(Exception):
    """A GroupMe call failed. `retryable` = worth trying again later."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------------------
# API client. One host, fixed paths, no redirects followed, the token only ever
# in a header (GroupMe's own advice), and never logged.
# ---------------------------------------------------------------------------

def _request(method, path, *, token=None, json=None, params=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Access-Token'] = token
    try:
        response = requests.request(
            method, f'{API_BASE}{path}', headers=headers, json=json,
            params=params, timeout=TIMEOUT_SECONDS, allow_redirects=False,
        )
    except requests.RequestException as e:
        # str(e) can include the URL but never the header, so it is safe.
        raise GroupMeError(f'Could not reach GroupMe ({type(e).__name__}).', retryable=True) from e

    if response.status_code == 401:
        raise GroupMeError('GroupMe rejected the access token (401). Paste a new one.')
    if response.status_code == 429 or response.status_code >= 500:
        raise GroupMeError(f'GroupMe is unavailable right now ({response.status_code}).', retryable=True)
    if response.status_code >= 400:
        raise GroupMeError(f'GroupMe said no ({response.status_code}): {_error_text(response)}')
    return response


def _error_text(response):
    try:
        errors = response.json().get('meta', {}).get('errors') or []
        if errors:
            return '; '.join(str(e) for e in errors)[:200]
    except ValueError:
        pass
    return (response.text or '')[:200]


def _payload(response):
    try:
        return response.json().get('response')
    except ValueError:
        return None


def get_me(token):
    """The GroupMe user the token belongs to: {'name': ..., 'id': ...}."""
    me = _payload(_request('GET', '/users/me', token=token)) or {}
    return {'name': me.get('name', ''), 'id': str(me.get('id', ''))}


def list_groups(token):
    """Groups the token's owner is in: [{'id', 'name', 'members'}], A→Z."""
    groups = []
    for page in range(1, 6):  # 5 x 100 groups is plenty for one person
        batch = _payload(_request(
            'GET', '/groups', token=token,
            params={'page': page, 'per_page': 100, 'omit': 'memberships'},
        )) or []
        for g in batch:
            groups.append({
                'id': str(g.get('id') or g.get('group_id') or ''),
                'name': g.get('name') or '(unnamed group)',
                'members': g.get('members_count'),
            })
        if len(batch) < 100:
            break
    return sorted((g for g in groups if g['id']), key=lambda g: g['name'].lower())


def create_bot(token, group_id, name):
    """Create a bot in `group_id`; returns its bot_id."""
    data = _payload(_request('POST', '/bots', token=token, json={
        'bot': {'name': name, 'group_id': str(group_id)},
    })) or {}
    bot_id = (data.get('bot') or {}).get('bot_id')
    if not bot_id:
        raise GroupMeError('GroupMe made the bot but did not return its id.')
    return bot_id


def destroy_bot(token, bot_id):
    _request('POST', '/bots/destroy', token=token, json={'bot_id': bot_id})


def post_as_bot(bot_id, text):
    """Post `text` to the bot's group. No token: the bot_id is the credential."""
    _request('POST', '/bots/post', json={'bot_id': bot_id, 'text': text[:MAX_MESSAGE_CHARS]})


# ---------------------------------------------------------------------------
# The audience rule
# ---------------------------------------------------------------------------

def channel_allowed(visible_to, channel):
    """May an announcement with this `visible_to` be posted to `channel`?"""
    audience = set(channel.audience or [])
    if not audience:
        # A channel nobody described is treated as "everyone is in it".
        from src.models.groupme import AUDIENCE_KEYS
        audience = set(AUDIENCE_KEYS)
    if not visible_to:
        return True
    return audience <= set(visible_to)


def blocked_reason(visible_to, channel):
    """Short reason a channel can't get this announcement, for the form/log."""
    if channel_allowed(visible_to, channel):
        return ''
    hidden = [label for key, label in _audience_labels() if key in (channel.audience or [])
              and key not in (visible_to or [])]
    if not hidden:
        return "Not everyone in this GroupMe can see it on Parliament"
    return f"{', '.join(hidden)} in this GroupMe can't see it on Parliament"


def _audience_labels():
    from src.models.groupme import AUDIENCE_CHOICES
    return AUDIENCE_CHOICES


def default_channels(visible_to, channels):
    """
    The channels to pre-tick: allowed ones, minus any whose audience is a
    strict subset of another allowed channel's (those people get it there).
    """
    allowed = [c for c in channels if channel_allowed(visible_to, c)]
    picks = []
    for c in allowed:
        mine = set(c.audience or [])
        if not any(mine < set(o.audience or []) for o in allowed if o.pk != c.pk):
            picks.append(c)
    return picks


# ---------------------------------------------------------------------------
# Message format
# ---------------------------------------------------------------------------

def format_announcement(announcement, include_full_text=True):
    """Plain text, at most MAX_MESSAGE_CHARS, always ending with the link."""
    from src.notifications import get_site_url

    link = f"{get_site_url()}{reverse('announcements')}"
    head = f"📢 {announcement.title.strip()}"
    tail = f"\n\n{link}"
    body = (announcement.content or '').strip() if include_full_text else ''
    if not body:
        return (head + tail)[:MAX_MESSAGE_CHARS]

    room = MAX_MESSAGE_CHARS - len(head) - len(tail) - 2  # 2 = "\n\n"
    if len(body) > room:
        body = body[:max(room - 1, 0)].rstrip() + '…'
    return f"{head}\n\n{body}{tail}"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def due_announcements_queryset():
    """Published, active, channels ticked, not yet handed to the bot."""
    from src.models import Announcement

    now = timezone.now()
    return (
        Announcement.objects
        .filter(is_active=True, groupme_dispatched_at__isnull=True,
                groupme_channels__isnull=False)
        .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
        .distinct()
    )


def dispatch_announcement(announcement_id):
    """
    Claim the announcement (once, ever) and create one queued GroupMeMessage per
    allowed channel. Returns the ids of the messages to send. Channels that are
    off, or that "Visible to" no longer allows, get a 'skipped' row so the log
    says why nothing appeared in that group.
    """
    from src.models import Announcement, GroupMeMessage, GroupMeSettings

    with transaction.atomic():
        announcement = (
            Announcement.objects.select_for_update(skip_locked=True)
            .filter(pk=announcement_id, groupme_dispatched_at__isnull=True)
            .first()
        )
        if announcement is None or not announcement.is_published():
            return []
        announcement.groupme_dispatched_at = timezone.now()
        announcement.save(update_fields=['groupme_dispatched_at'])

        settings_row = GroupMeSettings.get_instance()
        text = format_announcement(announcement, settings_row.include_full_text)
        to_send = []
        for channel in announcement.groupme_channels.all():
            if not channel.is_active:
                status, error = 'skipped', 'Channel is turned off.'
            elif not channel_allowed(announcement.visible_to, channel):
                status, error = 'skipped', blocked_reason(announcement.visible_to, channel) + '.'
            else:
                status, error = 'queued', ''
            message, created = GroupMeMessage.objects.get_or_create(
                announcement=announcement, channel=channel, kind='announcement',
                defaults={
                    'channel_name': channel.name, 'text': text, 'status': status,
                    'error': error, 'sent_by': announcement.posted_by,
                },
            )
            if created and status == 'queued':
                to_send.append(message.pk)
    return to_send


def deliver(message_id):
    """
    Post one queued message. Returns True on success. Raises GroupMeError with
    `retryable=True` for a transient failure so the task can retry; a
    permanent failure is recorded and returns False.
    """
    from src.models import GroupMeMessage

    retry_error = None
    with transaction.atomic():
        message = (
            GroupMeMessage.objects.select_for_update()
            .select_related('channel')
            .filter(pk=message_id, status__in=('queued', 'failed'))
            .first()
        )
        if message is None:
            return False
        if message.channel is None:
            message.status, message.error = 'failed', 'Channel was removed.'
            message.save(update_fields=['status', 'error'])
            return False
        message.attempts += 1
        try:
            post_as_bot(message.channel.bot_id, message.text)
        except GroupMeError as e:
            message.status, message.error = 'failed', str(e)[:500]
            message.save(update_fields=['status', 'error', 'attempts'])
            if not e.retryable:
                return False
            retry_error = e
        else:
            message.status, message.error, message.sent_at = 'sent', '', timezone.now()
            message.save(update_fields=['status', 'error', 'sent_at', 'attempts'])
            return True
    # Raised only AFTER the block commits: raising inside it would roll back
    # the 'failed' status and attempt count we just recorded.
    raise retry_error


def queue_send(message_ids):
    """Hand messages to Celery; if the broker is down they stay 'queued' and
    the dashboard's Retry (or the next sweep) picks them up."""
    from src.tasks.groupme import send_groupme_message

    for pk in message_ids:
        try:
            send_groupme_message.delay(pk)
        except Exception as e:  # broker down
            logger.warning(f'[groupme] could not queue message {pk}: {type(e).__name__}')


def schedule_dispatch(announcement):
    """After the announcement's transaction commits, post it if it's live."""
    if not announcement.is_published():
        return  # the 5-minute sweep posts it when publish_at arrives

    def _go():
        from src.tasks.groupme import dispatch_announcement_to_groupme
        try:
            dispatch_announcement_to_groupme.delay(announcement.pk)
        except Exception as e:  # broker down: the sweep is the safety net
            logger.warning(f'[groupme] could not queue announcement {announcement.pk}: {type(e).__name__}')

    transaction.on_commit(_go)
