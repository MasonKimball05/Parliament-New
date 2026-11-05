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
from django.core.cache import cache
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

#: v4.4.5 — the real `requests.request`, captured at import. A test run sets
#: PARLIAMENT_GROUPME_OFFLINE (src/cache_isolated_runner.py); while it is set,
#: a call that would reach the real api.groupme.com (nothing patched it) is
#: refused, so no test can ever post in, read or delete from a real group.
_REAL_REQUEST = requests.request
OFFLINE_ENV = 'PARLIAMENT_GROUPME_OFFLINE'


def _request(method, path, *, token=None, json=None, params=None, allow=()):
    import os
    if os.environ.get(OFFLINE_ENV) and requests.request is _REAL_REQUEST:
        raise GroupMeError('GroupMe is offline during tests (nothing patched requests.request).')
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

    if response.status_code in allow:
        return response
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


POSTING_FLAG = 'groupme_posting'


def posting_enabled():
    """v4.4.5: the site-wide "GroupMe posting" switch (Admin v2 → Feature Flags)."""
    from src.feature_flag_decorators import check_feature_enabled
    return check_feature_enabled(POSTING_FLAG)


def post_as_bot(bot_id, text):
    """Post `text` to the bot's group. No token: the bot_id is the credential.

    v4.4.5: EVERY GroupMe post goes through here, and nothing posts while the
    "GroupMe posting" feature flag is off. A bot always posts in the group's
    main chat (it is added to the group itself), visible to everyone in it.
    """
    if not posting_enabled():
        raise GroupMeError('GroupMe posting is turned off (Admin v2 → Feature Flags → GroupMe posting).')
    _request('POST', '/bots/post', json={'bot_id': bot_id, 'text': text[:MAX_MESSAGE_CHARS]})


def list_bots(token):
    """The bots the token's owner made: [{'bot_id', 'group_id', 'group_name', 'name'}].
    Read only: used by "Check connection", which never posts."""
    bots = _payload(_request('GET', '/bots', token=token)) or []
    return [{'bot_id': str(b.get('bot_id') or ''), 'group_id': str(b.get('group_id') or ''),
             'group_name': b.get('group_name') or '', 'name': b.get('name') or ''} for b in bots]


def recent_group_messages(token, group_id, before_id=None, limit=100):
    """One page of a group's messages, newest first (read only)."""
    params = {'limit': limit}
    if before_id:
        params['before_id'] = before_id
    response = _request('GET', f'/groups/{group_id}/messages', token=token, params=params)
    if response.status_code == 304:      # no messages (or none before before_id)
        return []
    return (_payload(response) or {}).get('messages') or []


#: How far back the lookup goes: 5 pages x 100 messages.
LOOKUP_PAGES = 5


def find_bot_message(token, group_id, text, posted_at=None):
    """
    The id of the bot post with exactly ``text`` in ``group_id``, or ''.
    A bot's post returns no id (POST /bots/post answers 202 with no body), so
    it is found in the group's recent messages: a bot message with the same
    text, sent no earlier than a few minutes before ``posted_at``.
    """
    want = (text or '')[:MAX_MESSAGE_CHARS].strip()
    earliest = int(posted_at.timestamp()) - 300 if posted_at else None
    before_id = None
    for _ in range(LOOKUP_PAGES):
        batch = recent_group_messages(token, group_id, before_id)
        if not batch:
            return ''
        for m in batch:
            if m.get('sender_type') == 'bot' and (m.get('text') or '').strip() == want:
                return str(m.get('id') or '')
        oldest = batch[-1]
        if earliest is not None and (oldest.get('created_at') or 0) < earliest:
            return ''
        before_id = oldest.get('id')
    return ''


def can_delete_in_group(token, group_id):
    """
    Whether the token's owner may delete the bot's posts in ``group_id``
    (owner or admin of the group). Returns (True/False, their name), or
    (None, '') if GroupMe couldn't say. Read only.
    """
    try:
        me = get_me(token)
        group = _payload(_request('GET', f'/groups/{group_id}', token=token)) or {}
    except GroupMeError:
        return None, ''
    if str(group.get('creator_user_id') or '') == me['id']:
        return True, me['name']
    for m in group.get('members') or []:
        if str(m.get('user_id') or '') == me['id']:
            roles = set(m.get('roles') or [])
            return bool(roles & {'admin', 'owner'}), me['name']
    return False, me['name']


def delete_group_message(token, group_id, message_id):
    """
    Delete one message from a group. Needs ``token``'s owner to be an owner or
    admin of the group (GroupMe answers 403 otherwise). Already gone (404)
    counts as deleted.
    """
    response = _request('DELETE', f'/conversations/{group_id}/messages/{message_id}',
                        token=token, allow=(403, 404))
    if response.status_code == 403:
        raise GroupMeError('GroupMe wouldn\'t let the connected account delete it (403): '
                           'that account must be an owner or admin of the group.')
    return True


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
    # v4.4.5: say who posted it (a bot's posts otherwise all read "Parliament").
    author = announcement.posted_by.get_display_name() if announcement.posted_by_id else ''
    if author:
        head += f"\nPosted by {author}"
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


# v4.4.5: an announcement created with "send email" goes to the email
# confirmation page first; its GroupMe post waits until that step is done
# (emails sent or skipped). The hold expires after HOLD_SECONDS so an
# abandoned confirmation page doesn't keep it off GroupMe forever.
HOLD_SECONDS = 30 * 60


def _hold_key(announcement_id):
    return f'groupme_hold_announcement_{announcement_id}'


def hold_announcement(announcement_id):
    cache.set(_hold_key(announcement_id), 1, HOLD_SECONDS)


def is_held(announcement_id):
    return bool(cache.get(_hold_key(announcement_id)))


def release_announcement(announcement):
    """The email step is done: post it to GroupMe now (if it has channels)."""
    cache.delete(_hold_key(announcement.pk))
    if announcement.groupme_dispatched_at is None and announcement.groupme_channels.exists():
        schedule_dispatch(announcement)


def dispatch_announcement(announcement_id):
    """
    Claim the announcement (once, ever) and create one queued GroupMeMessage per
    allowed channel. Returns the ids of the messages to send. Channels that are
    off, or that "Visible to" no longer allows, get a 'skipped' row so the log
    says why nothing appeared in that group.
    """
    from src.models import Announcement, GroupMeMessage, GroupMeSettings

    if is_held(announcement_id):
        return []   # still on the email confirmation step (see HOLD_SECONDS)
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
            # v3.50.1 — no select_related('channel') here: `channel` is a
            # nullable FK, so Django joins it with LEFT OUTER JOIN, and
            # PostgreSQL refuses "FOR UPDATE cannot be applied to the nullable
            # side of an outer join". Every send failed on prod Postgres with
            # NotSupportedError (not GroupMeError, so no retry; the row stayed
            # 'queued' and the sweep re-queued it forever). SQLite ignores
            # FOR UPDATE, which is why the tests passed. Reading `channel`
            # lazily costs one query and locks only the message row.
            GroupMeMessage.objects.select_for_update()
            .filter(pk=message_id, status__in=('queued', 'failed'))
            .first()
        )
        if message is None:
            return False
        if message.channel is None:
            message.status, message.error = 'failed', 'Channel was removed.'
            message.save(update_fields=['status', 'error'])
            return False
        # v3.50.1 — re-check the audience rule at the moment of posting. A
        # message can sit 'queued'/'failed' for minutes (Celery retries at 1, 2,
        # 4 min; the sweep re-queues; Retry on the dashboard) while an officer
        # turns the channel off, narrows "Visible to", or takes the post down.
        reason = _no_longer_postable(message)
        if reason:
            message.status, message.error = 'skipped', reason
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
            message.group_id = message.channel.group_id or ''
            message.save(update_fields=['status', 'error', 'sent_at', 'attempts', 'group_id'])
            transaction.on_commit(lambda: remember_message_id(message.pk))
            return True
    # Raised only AFTER the block commits: raising inside it would roll back
    # the 'failed' status and attempt count we just recorded.
    raise retry_error


def _no_longer_postable(message):
    """Why a queued/failed message must not be posted now, or '' if it still may."""
    channel = message.channel
    if not channel.is_active:
        return 'Channel was turned off before it was sent.'
    if message.kind == 'announcement':
        announcement = message.announcement
        if announcement is None:
            return 'The announcement was deleted before it was sent.'
        if not announcement.is_published():
            return 'The announcement was taken down before it was sent.'
        if not channel_allowed(announcement.visible_to, channel):
            return blocked_reason(announcement.visible_to, channel) + ' (changed before it was sent).'
    elif message.kind == 'event_reminder':
        event = message.event
        if event is None:
            return 'The event was deleted before the reminder was sent.'
        if not event.is_active or event.archived:
            return 'The event was hidden before the reminder was sent.'
        if event.date_time <= timezone.now():
            return 'The event had already started.'
        if not channel_allowed(event.visible_to, channel):
            return blocked_reason(event.visible_to, channel) + ' (changed before it was sent).'
    return ''


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


# ---------------------------------------------------------------------------
# Event reminders (v3.50.0)
# ---------------------------------------------------------------------------

def format_event_reminder(event):
    """`⏰ Reminder: Title` / when (and where) / the calendar link."""
    from src.notifications import get_site_url

    when = timezone.localtime(event.date_time)
    hour = when.strftime('%I').lstrip('0') or '12'
    line = f"{when.strftime('%A, %b')} {when.day} at {hour}:{when.strftime('%M %p')}"
    if event.location:
        line += f" · {event.location.strip()}"
    link = f"{get_site_url()}{reverse('calendar')}"
    head = f"⏰ Reminder: {event.title.strip()}"
    # v4.4.5: who put the event on the calendar.
    if event.created_by_id:
        line += f"\nPosted by {event.created_by.get_display_name()}"
    return f"{head}\n{line}\n\n{link}"[:MAX_MESSAGE_CHARS]


def due_event_reminders():
    """Events whose GroupMe reminder time has come and that haven't had it."""
    from datetime import timedelta
    from src.models import Event

    now = timezone.now()
    longest = max(Event.GROUPME_REMINDER_HOURS)
    candidates = (
        Event.objects
        .filter(is_active=True, archived=False, groupme_reminded_at__isnull=True,
                groupme_channels__isnull=False,
                date_time__gt=now, date_time__lte=now + timedelta(hours=longest))
        .distinct()
    )
    return [e.pk for e in candidates
            if e.date_time - timedelta(hours=e.groupme_reminder_hours) <= now]


def dispatch_event_reminder(event_id):
    """Claim the event's reminder (once, ever) and queue one message per allowed channel."""
    from src.models import Event, GroupMeMessage

    with transaction.atomic():
        event = (
            Event.objects.select_for_update(skip_locked=True)
            .filter(pk=event_id, groupme_reminded_at__isnull=True, is_active=True)
            .first()
        )
        if event is None or event.date_time <= timezone.now():
            return []
        event.groupme_reminded_at = timezone.now()
        event.save(update_fields=['groupme_reminded_at'])
        text = format_event_reminder(event)
        to_send = []
        for channel in event.groupme_channels.all():
            if not channel.is_active:
                status, error = 'skipped', 'Channel is turned off.'
            elif not channel_allowed(event.visible_to, channel):
                status, error = 'skipped', blocked_reason(event.visible_to, channel) + '.'
            else:
                status, error = 'queued', ''
            message, created = GroupMeMessage.objects.get_or_create(
                event=event, channel=channel, kind='event_reminder',
                defaults={'channel_name': channel.name, 'text': text, 'status': status,
                          'error': error, 'sent_by': event.created_by},
            )
            if created and status == 'queued':
                to_send.append(message.pk)
    return to_send


def save_event_channels(request, events):
    """
    Store the ticked channels (only ones the audience rule allows) and the
    "hours before" on each of `events` (one event, or a recurring series). An
    event whose reminder already went out is left alone.
    """
    from src.models import Event, GroupMeChannel

    ids = [int(i) for i in request.POST.getlist('groupme_channel_ids') if i.isdecimal()]
    channels = list(GroupMeChannel.objects.filter(is_active=True, pk__in=ids))
    raw = request.POST.get('groupme_reminder_hours', '')
    hours = int(raw) if raw.isdecimal() and int(raw) in Event.GROUPME_REMINDER_HOURS else 24
    for event in events:
        if event.groupme_reminded_at:
            continue
        event.groupme_channels.set([c for c in channels if channel_allowed(event.visible_to, c)])
        if event.groupme_reminder_hours != hours:
            event.groupme_reminder_hours = hours
            event.save(update_fields=['groupme_reminder_hours'])


# ---------------------------------------------------------------------------
# Taking posts down (v4.4.5)
#
# Exec feedback 11-05-26: deleting an announcement or event on Parliament
# should delete it (and its reminder) on GroupMe too. Deleting another
# member's message (the bot's) needs the connected GroupMe account to be an
# owner or admin of the group; GroupMe refuses otherwise, and the log says so.
# ---------------------------------------------------------------------------

def _token():
    from src.models import GroupMeSettings
    return GroupMeSettings.get_instance().access_token or ''


def remember_message_id(message_pk):
    """After a post: find its GroupMe id and keep it (best effort, never raises)."""
    from src.models import GroupMeMessage
    message = GroupMeMessage.objects.filter(pk=message_pk, status='sent').first()
    token = _token()
    if message is None or message.groupme_message_id or not token or not message.group_id:
        return
    try:
        found = find_bot_message(token, message.group_id, message.text, message.sent_at)
    except GroupMeError as e:
        logger.info(f'[groupme] could not look up message {message_pk}: {e}')
        return
    if found:
        GroupMeMessage.objects.filter(pk=message_pk).update(groupme_message_id=found)


def messages_to_take_down(**owner):
    """The pks of an announcement's or event's posts (``announcement=`` / ``event=``)."""
    from src.models import GroupMeMessage
    return list(GroupMeMessage.objects.filter(status__in=('queued', 'failed', 'sent'), **owner)
                .values_list('pk', flat=True))


def take_down(message_pk):
    """
    Delete one post from its group because what it was about was deleted on
    Parliament. A post that never went out is marked skipped instead. Raises
    GroupMeError(retryable=True) for a transient failure; a permanent one is
    recorded in the log.
    """
    from src.models import GroupMeMessage
    with transaction.atomic():
        message = GroupMeMessage.objects.select_for_update().filter(pk=message_pk).first()
        if message is None or message.status in ('deleted', 'skipped'):
            return False
        if message.status in ('queued', 'failed'):
            message.status, message.error = 'skipped', 'Deleted on Parliament before it was sent.'
            message.save(update_fields=['status', 'error'])
            return False

    token = _token()
    group_id = message.group_id or (message.channel.group_id if message.channel_id else '')
    def _fail(text):
        GroupMeMessage.objects.filter(pk=message_pk).update(error=f'Not deleted from GroupMe: {text}'[:500])
        return False
    if not token:
        return _fail('no GroupMe account is connected.')
    if not group_id:
        return _fail('the group is unknown.')
    try:
        message_id = message.groupme_message_id or find_bot_message(token, group_id, message.text, message.sent_at)
        if not message_id:
            return _fail('it wasn\'t found in the group\'s recent messages (already deleted, or too old).')
        delete_group_message(token, group_id, message_id)
    except GroupMeError as e:
        if e.retryable:
            _fail(str(e))
            raise
        return _fail(str(e))
    GroupMeMessage.objects.filter(pk=message_pk).update(
        status='deleted', deleted_at=timezone.now(), error='', groupme_message_id=message_id, group_id=group_id)
    return True


def queue_take_down(message_pks):
    """After the delete commits, take each post down (Celery; inline if the broker is down)."""
    if not message_pks:
        return
    pks = list(message_pks)

    def _go():
        from src.tasks.groupme import take_down_groupme_message
        for pk in pks:
            try:
                take_down_groupme_message.delay(pk)
            except Exception as e:  # broker down
                logger.warning(f'[groupme] could not queue take-down {pk}: {type(e).__name__}')

    transaction.on_commit(_go)
