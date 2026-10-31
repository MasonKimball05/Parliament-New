"""
The GroupMe bot dashboard in the app (v4.4.0): the officer page at
/officers/groupme/, through the same functions as the website
(src/view/officer/groupme.py: ``dashboard_data`` and one ``*_action`` per
form), so every change is checked, done and logged the same way.

* ``GET officer/groupme/`` — the connection (connected or not, whose account,
  bot name, full text on or off; never the token), the "posts waiting"
  warning, the channels (bot ids masked to their last 4, who's in each, the
  last post), the connected account's groups, the audience choices and the
  last 50 posts.
* ``POST officer/groupme/settings/`` ``{access_token, clear_token, bot_name,
  include_full_text}`` — connect (the token is checked with GroupMe) or
  disconnect the account, rename new bots, full text on or off.
  ``{detail}`` is null when nothing changed.
* ``POST officer/groupme/channels/`` ``{name, audience, group_id}`` adds the
  bot to one of the account's groups; ``{name, audience, bot_id,
  group_name}`` registers a bot made on dev.groupme.com instead. 201.
* ``POST officer/groupme/channels/<id>/edit/`` ``{name, audience,
  is_active}``; ``.../move/`` ``{group_id}`` (new bot there, old one removed);
  ``.../remove/``; ``.../test/`` (a test message, once per 30 seconds).
* ``POST officer/groupme/messages/<id>/retry/`` — resend a failed post.

Each action answers ``{detail}``, the website's message for it; a refusal is a
400 ``refused`` with the website's message as ``detail``. Officers and chairs
(``officer_required``, the same rule as ``can_manage_groupme``).
"""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer_tools import _Officers
from src.models import GroupMeChannel
from src.view.officer import groupme as gm


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _flag(value):
    return value is True or value in ('on', 'true', '1', 1)


def _list(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)] if value else []


def _run(action, status=200):
    try:
        detail = action()
    except gm.GroupMeActionError as e:
        raise BadRequest(str(e), code='refused')
    return Response({'detail': detail}, status=status)


def _iso(dt):
    return dt.isoformat() if dt else None


def _message_json(m):
    return {
        'kind': m.kind,
        'kind_label': m.get_kind_display(),
        'status': m.status,
        'status_label': m.get_status_display(),
        'error': m.error,
        'created_at': m.created_at.isoformat(),
    } if m else None


def _what(m):
    """The log's "What" column, as the web table words it."""
    if m.announcement_id:
        return m.announcement.title
    if m.event_id:
        return f'Reminder: {m.event.title}'
    return m.get_kind_display()


class GroupMeDashboardView(_Officers):
    web_page = 'groupme_dashboard'

    def get(self, request):
        d = gm.dashboard_data()
        s = d['settings_row']
        return Response({
            'connection': {
                'connected': s.has_token,
                'owner_name': s.token_owner_name,
                'bot_name': s.bot_name,
                'include_full_text': s.include_full_text,
            },
            'stuck': {'count': d['stuck_count'], 'oldest': _iso(d['stuck_oldest']),
                      'minutes': d['stuck_minutes']},
            'channels': [{
                'id': c.pk,
                'name': c.name,
                'is_active': c.is_active,
                'group_id': c.group_id,
                'group_name': c.group_name,
                'bot': c.masked_bot_id,
                'bot_created_here': c.bot_created_here,
                'audience': [k for k in gm.AUDIENCE_KEYS if k in (c.audience or [])],
                'audience_labels': c.audience_labels(),
                'last_message': _message_json(c.last_message),
            } for c in d['channels']],
            'groups': [{'id': g['id'], 'name': g['name'], 'members': g.get('members')} for g in d['groups']],
            'groups_error': d['groups_error'],
            'used_group_ids': sorted(d['used_group_ids']),
            'audiences': [{'value': k, 'label': label} for k, label in d['AUDIENCE_CHOICES']],
            'log': [{
                'id': m.pk,
                'created_at': m.created_at.isoformat(),
                'channel_name': m.channel_name,
                'what': _what(m),
                'kind': m.kind,
                'status': m.status,
                'status_label': m.get_status_display(),
                'error': m.error,
                'attempts': m.attempts,
                # The web page's Retry button; the action re-checks the channel.
                'can_retry': m.status == 'failed' and m.kind != 'test',
            } for m in d['log']],
        })


class GroupMeSettingsView(_Officers):
    web_page = 'groupme_save_settings'

    def post(self, request):
        body = _body(request)
        return _run(lambda: gm.save_settings_action(
            request,
            access_token=str(body.get('access_token') or ''),
            clear_token=_flag(body.get('clear_token')),
            bot_name=str(body.get('bot_name') or ''),
            include_full_text=_flag(body.get('include_full_text')),
        ))


class GroupMeAddChannelView(_Officers):
    web_page = 'groupme_add_channel'

    def post(self, request):
        body = _body(request)
        return _run(lambda: gm.add_channel_action(
            request,
            name=str(body.get('name') or ''),
            audience=_list(body.get('audience')),
            bot_id=str(body.get('bot_id') or ''),
            group_name=str(body.get('group_name') or ''),
            group_id=str(body.get('group_id') or ''),
        )[1], status=201)


class GroupMeEditChannelView(_Officers):
    web_page = 'groupme_edit_channel'

    def post(self, request, channel_id):
        channel = get_object_or_404(GroupMeChannel, pk=channel_id)
        body = _body(request)
        return _run(lambda: gm.edit_channel_action(
            request, channel,
            name=str(body.get('name') or ''),
            audience=_list(body.get('audience')),
            is_active=_flag(body.get('is_active')),
        ))


class GroupMeMoveChannelView(_Officers):
    web_page = 'groupme_move_channel'

    def post(self, request, channel_id):
        channel = get_object_or_404(GroupMeChannel, pk=channel_id)
        group_id = str(_body(request).get('group_id') or '')
        return _run(lambda: gm.move_channel_action(request, channel, group_id=group_id))


class GroupMeRemoveChannelView(_Officers):
    web_page = 'groupme_remove_channel'

    def post(self, request, channel_id):
        channel = get_object_or_404(GroupMeChannel, pk=channel_id)
        return _run(lambda: gm.remove_channel_action(request, channel))


class GroupMeTestChannelView(_Officers):
    web_page = 'groupme_test_channel'

    def post(self, request, channel_id):
        channel = get_object_or_404(GroupMeChannel, pk=channel_id)
        return _run(lambda: gm.send_test_action(request, channel))


class GroupMeRetryMessageView(_Officers):
    web_page = 'groupme_retry_message'

    def post(self, request, message_id):
        message = get_object_or_404(gm.retryable_messages(), pk=message_id)
        return _run(lambda: gm.retry_message_action(request, message))
