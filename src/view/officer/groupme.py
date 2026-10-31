"""
v3.49.0 — the GroupMe bot dashboard (/officers/groupme/). Officers only
(Mason's call, 10-17-26), every change is a POST and lands in the activity log.

From here an officer can:
  * connect a GroupMe account (paste its access token; it's checked against
    GroupMe, encrypted, and never shown again);
  * add the bot to one of that account's groups, or register a bot that was
    made by hand on dev.groupme.com (paste its bot ID; no token needed);
  * say who is in each group (brothers / pledges / advisors), which decides
    what may be posted there (src/groupme.py, "the audience rule");
  * MOVE a channel to a different group when the chapter's GroupMe changes:
    a new bot is made in the new group and the old one is removed;
  * send a test message, turn a channel off, remove it, and retry failures.

v4.4.0 — the app's GroupMe screen (src/mobile/views_groupme.py) does all of
this too, through the same functions: `dashboard_data` for the page and one
`*_action` per form below. Each action returns the message to show and raises
`GroupMeActionError` with the message for a refusal; the web views turn those
into flash messages and a redirect, the app into JSON.
"""
import re
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Min
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.decorators import log_function_call, officer_required
from src.groupme import (
    GroupMeError, create_bot, destroy_bot, get_me, list_groups, post_as_bot,
    queue_send,
)
from src.models import ActivityLog, GroupMeChannel, GroupMeMessage, GroupMeSettings
from src.models.groupme import AUDIENCE_CHOICES, AUDIENCE_KEYS

GROUPS_CACHE_KEY = 'groupme_groups_list'
GROUPS_CACHE_SECONDS = 120
TEST_COOLDOWN_SECONDS = 30
LOG_ROWS = 50
STUCK_MINUTES = 10  # v3.51.0: "N posts waiting" warning on the dashboard

_GROUP_ID_RE = re.compile(r'^\d{1,50}$')
_BOT_ID_RE = re.compile(r'^[A-Za-z0-9]{6,100}$')


def _log(request, description, channel=None):
    ActivityLog.log_activity(
        action_type='settings_changed', user=request.user, request=request,
        description=f'GroupMe: {description}',
        object_type='GroupMeChannel' if channel else 'GroupMeSettings',
        object_id=channel.pk if channel else None,
        object_repr=channel.name if channel else '',
    )


def _settings():
    return GroupMeSettings.get_instance()


def _groups(settings_row):
    """The token owner's groups, cached briefly. (groups, error_message)."""
    if not settings_row.has_token:
        return [], ''
    groups = cache.get(GROUPS_CACHE_KEY)
    if groups is not None:
        return groups, ''
    try:
        groups = list_groups(settings_row.access_token)
    except GroupMeError as e:
        return [], str(e)
    cache.set(GROUPS_CACHE_KEY, groups, GROUPS_CACHE_SECONDS)
    return groups, ''


def _group_name(settings_row, group_id):
    groups, _ = _groups(settings_row)
    return next((g['name'] for g in groups if g['id'] == group_id), '')


def clean_audience(values):
    """The known audience keys among `values`, in the usual order."""
    return [k for k in AUDIENCE_KEYS if k in values]


def clean_name(value):
    return (value or '').strip()[:100]


class GroupMeActionError(Exception):
    """An action refused or failed; `str(e)` is the message for the officer (v4.4.0)."""


# ── The page and its actions (v4.4.0: shared with src/mobile/views_groupme.py) ──

def dashboard_data():
    """Everything the dashboard shows: the connection, channels, groups and log."""
    settings_row = _settings()
    groups, groups_error = _groups(settings_row)
    channels = list(GroupMeChannel.objects.all())
    used_group_ids = {c.group_id for c in channels if c.group_id}
    for c in channels:
        c.last_message = (
            GroupMeMessage.objects.filter(channel=c).order_by('-created_at').first()
        )
    log = (
        GroupMeMessage.objects.select_related('announcement', 'event')
        .only('channel_name', 'kind', 'status', 'error', 'created_at', 'sent_at',
              'attempts', 'announcement__title', 'announcement__id',
              'event__title', 'event__id')
        .order_by('-created_at')[:LOG_ROWS]
    )
    # v3.51.0 — posts that should have gone out by now. Retries run at 1, 2
    # and 4 minutes and the sweep re-queues every 5, so anything still
    # 'queued' after STUCK_MINUTES is not going to post by itself (worker
    # down, or deliver() failing the way v3.49.0 did on Postgres).
    stuck_cutoff = timezone.now() - timedelta(minutes=STUCK_MINUTES)
    stuck = GroupMeMessage.objects.filter(status='queued', created_at__lt=stuck_cutoff).aggregate(
        count=Count('pk'), oldest=Min('created_at'))
    return {
        'stuck_count': stuck['count'],
        'stuck_oldest': stuck['oldest'],
        'stuck_minutes': STUCK_MINUTES,
        'settings_row': settings_row,
        'channels': channels,
        'groups': groups,
        'groups_error': groups_error,
        'used_group_ids': used_group_ids,
        'log': log,
        'AUDIENCE_CHOICES': AUDIENCE_CHOICES,
    }


def save_settings_action(request, *, access_token='', clear_token=False, bot_name='',
                         include_full_text=False):
    """Connect/disconnect the account, rename new bots, full text on/off.
    Returns the message, or None when nothing changed."""
    settings_row = _settings()
    changes = []

    if clear_token:
        settings_row.access_token = ''
        settings_row.token_owner_name = ''
        changes.append('removed the access token')
    else:
        token = (access_token or '').strip()
        if token:
            if len(token) > 200 or not re.fullmatch(r'[A-Za-z0-9]+', token):
                raise GroupMeActionError("That doesn't look like a GroupMe access token.")
            try:
                me = get_me(token)
            except GroupMeError as e:
                raise GroupMeActionError(f'Token not saved: {e}')
            settings_row.access_token = token
            settings_row.token_owner_name = me['name'][:200]
            changes.append(f"connected {me['name'] or 'a GroupMe account'}")

    bot_name = (bot_name or '').strip()[:30]
    if bot_name and bot_name != settings_row.bot_name:
        settings_row.bot_name = bot_name
        changes.append(f'bot name → {bot_name}')
    include_full_text = bool(include_full_text)
    if include_full_text != settings_row.include_full_text:
        settings_row.include_full_text = include_full_text
        changes.append('full text ' + ('on' if include_full_text else 'off'))

    settings_row.updated_by = request.user
    settings_row.save()
    cache.delete(GROUPS_CACHE_KEY)
    if changes:
        _log(request, '; '.join(changes))
        return 'GroupMe settings saved.'
    return None


def add_channel_action(request, *, name, audience, bot_id='', group_name='', group_id=''):
    """Add the bot to a group (needs the token), or register an existing bot.
    Returns (channel, message)."""
    settings_row = _settings()
    name = clean_name(name)
    audience = clean_audience(audience)
    if not name or not audience:
        raise GroupMeActionError('Give the channel a name and say who is in that GroupMe.')

    existing_bot_id = (bot_id or '').strip()
    if existing_bot_id:
        if not _BOT_ID_RE.fullmatch(existing_bot_id):
            raise GroupMeActionError("That doesn't look like a GroupMe bot ID.")
        channel = GroupMeChannel.objects.create(
            name=name, bot_id=existing_bot_id, bot_created_here=False,
            group_name=(group_name or '').strip()[:200],
            audience=audience, created_by=request.user,
        )
        _log(request, f'registered an existing bot as "{name}"', channel)
        return channel, f'"{name}" added. Send a test message to check it.'

    group_id = (group_id or '').strip()
    if not settings_row.has_token:
        raise GroupMeActionError('Connect a GroupMe account first, or paste a bot ID.')
    if not _GROUP_ID_RE.fullmatch(group_id):
        raise GroupMeActionError('Pick a group.')
    try:
        new_bot_id = create_bot(settings_row.access_token, group_id, settings_row.bot_name)
    except GroupMeError as e:
        raise GroupMeActionError(f'Could not add the bot: {e}')
    channel = GroupMeChannel.objects.create(
        name=name, group_id=group_id, group_name=_group_name(settings_row, group_id),
        bot_id=new_bot_id, bot_created_here=True, audience=audience, created_by=request.user,
    )
    _log(request, f'added the bot to {channel.group_name or group_id} as "{name}"', channel)
    return channel, f'Bot added to {channel.group_name or "the group"}.'


def edit_channel_action(request, channel, *, name, audience, is_active):
    name = clean_name(name)
    audience = clean_audience(audience)
    if not name or not audience:
        raise GroupMeActionError('A channel needs a name and at least one group of people.')
    channel.name = name
    channel.audience = audience
    channel.is_active = bool(is_active)
    channel.save(update_fields=['name', 'audience', 'is_active', 'updated_at'])
    _log(request, f'edited "{name}" (who: {", ".join(channel.audience_labels())}; '
                  f'{"on" if channel.is_active else "off"})', channel)
    return f'"{name}" saved.'


def move_channel_action(request, channel, *, group_id):
    """
    The chapter moved to a different GroupMe: make a bot in the new group and
    remove the old one. The channel (name, audience, history, which
    announcements tick it) stays the same.
    """
    settings_row = _settings()
    group_id = (group_id or '').strip()
    if not settings_row.has_token:
        raise GroupMeActionError('Connect a GroupMe account first.')
    if not _GROUP_ID_RE.fullmatch(group_id) or group_id == channel.group_id:
        raise GroupMeActionError('Pick a different group.')
    try:
        new_bot_id = create_bot(settings_row.access_token, group_id, settings_row.bot_name)
    except GroupMeError as e:
        raise GroupMeActionError(f'Could not add the bot to the new group: {e}')

    old_note = _retire_bot(settings_row, channel)
    old_group = channel.group_name or channel.group_id or 'the old group'
    channel.group_id = group_id
    channel.group_name = _group_name(settings_row, group_id)
    channel.bot_id = new_bot_id
    channel.bot_created_here = True
    channel.save(update_fields=['group_id', 'group_name', 'bot_id', 'bot_created_here', 'updated_at'])
    _log(request, f'moved "{channel.name}" from {old_group} to {channel.group_name or group_id}', channel)
    return f'"{channel.name}" now posts to {channel.group_name or "the new group"}.{old_note}'


def _retire_bot(settings_row, channel):
    """Try to remove the channel's bot from GroupMe. Returns a note for the officer."""
    if not channel.bot_created_here:
        return (' The old bot was made outside Parliament, so remove it from that '
                "group's settings in GroupMe.")
    if not settings_row.has_token:
        return ' Remove the old bot from the group in GroupMe (no token connected).'
    try:
        destroy_bot(settings_row.access_token, channel.bot_id)
    except GroupMeError as e:
        # Usually: the bot was made from a different person's GroupMe account
        # (e.g. last year's officer). Only its owner can delete it.
        return (f' Could not remove the old bot ({e}); it may belong to a different '
                'GroupMe account. Remove it from the group in GroupMe.')
    return ''


def remove_channel_action(request, channel):
    note = _retire_bot(_settings(), channel)
    name = channel.name
    _log(request, f'removed "{name}"', channel)
    channel.delete()
    return f'"{name}" removed.{note}'


def send_test_action(request, channel):
    if not cache.add(f'groupme_test_{channel.pk}', 1, TEST_COOLDOWN_SECONDS):
        raise GroupMeActionError(f'Wait {TEST_COOLDOWN_SECONDS} seconds between test messages.')
    text = f'✅ Test from Parliament: this group is connected as "{channel.name}".'
    message = GroupMeMessage.objects.create(
        channel=channel, channel_name=channel.name, kind='test', text=text,
        sent_by=request.user, attempts=1,
    )
    try:
        post_as_bot(channel.bot_id, text)
    except GroupMeError as e:
        message.status, message.error = 'failed', str(e)[:500]
        message.save(update_fields=['status', 'error'])
        raise GroupMeActionError(f'Test failed: {e}')
    message.status, message.sent_at = 'sent', timezone.now()
    message.save(update_fields=['status', 'sent_at'])
    return f'Test sent to "{channel.name}". Check the group.'


def retryable_messages():
    """The posts the dashboard's Retry button can resend (any status)."""
    return GroupMeMessage.objects.filter(kind__in=('announcement', 'event_reminder'))


def retry_message_action(request, message):
    if message.status != 'failed' or message.channel_id is None:
        raise GroupMeActionError('Only a failed post to a channel that still exists can be retried.')
    queue_send([message.pk])
    return 'Retrying. Refresh in a minute to see the result.'


# ── Web views ────────────────────────────────────────────────────────────────

def _done(request, run):
    """Run an action, flash its message (error on refusal), back to the dashboard."""
    try:
        messages.success(request, run())
    except GroupMeActionError as e:
        messages.error(request, str(e))
    return redirect('groupme_dashboard')


@login_required
@officer_required
@log_function_call
def groupme_dashboard(request):
    return render(request, 'officer/groupme_dashboard.html', dashboard_data())


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_save_settings(request):
    try:
        saved = save_settings_action(
            request,
            access_token=request.POST.get('access_token'),
            clear_token=request.POST.get('clear_token') == 'on',
            bot_name=request.POST.get('bot_name'),
            include_full_text=request.POST.get('include_full_text') == 'on',
        )
    except GroupMeActionError as e:
        messages.error(request, str(e))
        return redirect('groupme_dashboard')
    if saved:
        messages.success(request, saved)
    else:
        messages.info(request, 'Nothing changed.')
    return redirect('groupme_dashboard')


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_add_channel(request):
    """Add the bot to a group (needs the token), or register an existing bot."""
    return _done(request, lambda: add_channel_action(
        request,
        name=request.POST.get('name'),
        audience=request.POST.getlist('audience'),
        bot_id=request.POST.get('bot_id'),
        group_name=request.POST.get('group_name'),
        group_id=request.POST.get('group_id'),
    )[1])


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_edit_channel(request, channel_id):
    channel = get_object_or_404(GroupMeChannel, pk=channel_id)
    return _done(request, lambda: edit_channel_action(
        request, channel,
        name=request.POST.get('name'),
        audience=request.POST.getlist('audience'),
        is_active=request.POST.get('is_active') == 'on',
    ))


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_move_channel(request, channel_id):
    channel = get_object_or_404(GroupMeChannel, pk=channel_id)
    return _done(request, lambda: move_channel_action(request, channel, group_id=request.POST.get('group_id')))


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_remove_channel(request, channel_id):
    channel = get_object_or_404(GroupMeChannel, pk=channel_id)
    return _done(request, lambda: remove_channel_action(request, channel))


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_test_channel(request, channel_id):
    channel = get_object_or_404(GroupMeChannel, pk=channel_id)
    return _done(request, lambda: send_test_action(request, channel))


@login_required
@officer_required
@require_POST
@log_function_call
def groupme_retry_message(request, message_id):
    message = get_object_or_404(retryable_messages(), pk=message_id)
    return _done(request, lambda: retry_message_action(request, message))
