"""
The app's Chat tab (mobile plan, Phase 3).

* ``GET  chat/channels/`` — channels the member can read: unread count, last
  message, whether they can write, their notification level. Newest activity
  first.
* ``GET  chat/channels/<id>/messages/?before=<msg id>|since=<msg id>&limit=``
  — a page of messages (default the latest 50), oldest first, each flagged
  ``can_edit`` / ``can_delete``. Fetching marks the member active in the
  channel (no pushes while they're looking), like the web chat's polling.
* ``POST chat/channels/<id>/send/`` ``{message}``
* ``POST chat/channels/<id>/messages/<mid>/edit/`` ``{message}`` and
  ``…/delete/``
* ``POST chat/channels/<id>/read/`` ``{up_to?: msg id}`` — moves the read
  marker (default: the latest message).
* ``GET|POST chat/channels/<id>/notification-level/`` ``{level}``

**Access.** Only ``ChatChannel``'s own rule (``has_access`` / ``can_write``,
one implementation), with NO admin override: the app has no "View All
Channels" mode, so an admin sees what the rule admits them to, Kai committee
chat included only if they are on it. A channel the member can't read is a
404, so channel ids can't be probed. Behind the ``chats`` flag and the
``chat_index`` page toggle / pledge restriction, as on the web.

**One implementation.** Sending, editing, deleting and read markers are
``src/chat_service.py``, the web chat's code: the write rule, the 5-per-3-s
rate limit, @mentions only reaching people who can read the channel, push
text hidden for non-open channels, the websocket broadcast.

**Live updates.** The app opens ``/ws/chat/<id>/`` with a ticket from
``ws-ticket/`` (src/mobile/ws.py); events are the web's (``chat.message``,
``chat.edit``, ``chat.delete``, ``chat.typing``). Until websockets are
confirmed in production the app polls ``messages/?since=``.
"""
from django.db.models import OuterRef, Subquery
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response

from src import chat_service
from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.models import ChatChannel, ChatMessage, ChatReadReceipt
from src.models.chat import ChatNotificationPreference

PAGE = 50
MAX_PAGE = 100
PREVIEW = 120


class ChatRefused(exceptions.APIException):
    status_code = 400
    default_code = 'chat_refused'

    def __init__(self, code, message, status=400):
        super().__init__(message, code=code)
        self.status_code = status


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _ChatBase(MobileAPIView):
    web_page = 'chat_index'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_feature_enabled('chats'):
            raise exceptions.PermissionDenied('Chat is off.', code='feature_disabled')

    def channel(self, request, channel_id):
        channel = (ChatChannel.objects.select_related('committee')
                   .filter(pk=channel_id, is_active=True).first())
        # Normal access only — no admin override in the app.
        if channel is None or not channel.has_access(request.user):
            raise Http404
        return channel

    def message(self, channel, message_id):
        message = (chat_service.messages_qs(channel).filter(pk=message_id).first())
        if message is None:
            raise Http404
        return message


def _serialize(msg, user, channel, perms):
    return {
        **chat_service.serialize(msg, user),
        'can_edit': chat_service.can_edit(channel, msg, user, perms),
        'can_delete': chat_service.can_delete(channel, msg, user, perms),
    }


class ChannelListView(_ChatBase):

    def get(self, request):
        user = request.user
        channels = list(ChatChannel.objects.filter(is_active=True).select_related('committee'))
        readable = ChatChannel.access_map(channels, user)
        channels = [c for c in channels if readable[c.pk]]
        ids = [c.pk for c in channels]

        unread = ChatChannel.unread_map(channels, user)
        # can_write, answered by the same rule for every channel at once.
        write_ctx = ChatChannel.access_context(user, channel_ids=ids, flag='can_write')
        levels = dict(ChatNotificationPreference.objects
                      .filter(user=user, channel_id__in=ids).values_list('channel_id', 'level'))
        latest = (ChatMessage.objects.filter(channel=OuterRef('pk'), is_deleted=False)
                  .order_by('-created_at').values('pk')[:1])
        last_ids = dict(ChatChannel.objects.filter(pk__in=ids)
                        .annotate(last_id=Subquery(latest)).values_list('pk', 'last_id'))
        last = {m.channel_id: m for m in ChatMessage.objects
                .filter(pk__in=[i for i in last_ids.values() if i])
                .select_related('sender').only('id', 'channel_id', 'message', 'created_at',
                                               'sender__name', 'sender__user_id')}

        out = []
        for c in channels:
            m = last.get(c.pk)
            out.append({
                'id': c.pk,
                'name': c.name,
                'description': c.description,
                'icon': c.icon,
                'color': c.color,
                'channel_type': c.channel_type,
                'access_type': c.access_type,
                'committee_code': c.committee.code if c.committee else None,
                'is_read_only': c.is_read_only,
                'can_write': (not c.is_read_only and
                              (c.access_type == 'open' or c._has_access_cached(write_ctx))),
                'unread': unread.get(c.pk, 0),
                'notification_level': levels.get(c.pk, ChatNotificationPreference.LEVEL_ALL),
                'last_message': ({
                    'id': m.id,
                    'sender_name': m.sender.name,
                    'preview': m.message[:PREVIEW],
                    'created_at': m.created_at.isoformat(),
                } if m else None),
            })
        out.sort(key=lambda c: c['last_message']['created_at'] if c['last_message'] else '',
                 reverse=True)
        return Response({'channels': out})


class MessageListView(_ChatBase):

    def get(self, request, channel_id):
        user = request.user
        channel = self.channel(request, channel_id)
        qs = chat_service.messages_qs(channel)
        limit = min(max(_int(request.query_params.get('limit')) or PAGE, 1), MAX_PAGE)
        since = _int(request.query_params.get('since'))
        before = _int(request.query_params.get('before'))
        if since is not None:
            # Newer than what the app has, oldest first.
            rows = list(qs.filter(pk__gt=since).order_by('pk')[:limit + 1])
            has_more = len(rows) > limit
            rows = rows[:limit]
        else:
            if before is not None:
                qs = qs.filter(pk__lt=before)
            rows = list(qs.order_by('-pk')[:limit + 1])
            has_more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
        receipt = chat_service.touch_presence(channel, user)
        perms = chat_service.channel_permissions(channel, user)
        return Response({
            'messages': [_serialize(m, user, channel, perms) for m in rows],
            'has_more': has_more,
            'last_read_message_id': receipt.last_read_message_id,
        })


class SendView(_ChatBase):

    def post(self, request, channel_id):
        channel = self.channel(request, channel_id)
        text = request.data.get('message') if isinstance(request.data, dict) else None
        try:
            message = chat_service.send_message(channel, request.user, text)
        except chat_service.ChatError as exc:
            raise ChatRefused(exc.code, str(exc), exc.status)
        perms = chat_service.channel_permissions(channel, request.user)
        return Response({'message': _serialize(message, request.user, channel, perms)})


class EditView(_ChatBase):

    def post(self, request, channel_id, message_id):
        channel = self.channel(request, channel_id)
        message = self.message(channel, message_id)
        text = request.data.get('message') if isinstance(request.data, dict) else None
        try:
            chat_service.edit_message(channel, message, request.user, text)
        except chat_service.ChatError as exc:
            raise ChatRefused(exc.code, str(exc), exc.status)
        perms = chat_service.channel_permissions(channel, request.user)
        return Response({'message': _serialize(message, request.user, channel, perms)})


class DeleteView(_ChatBase):

    def post(self, request, channel_id, message_id):
        channel = self.channel(request, channel_id)
        message = self.message(channel, message_id)
        try:
            chat_service.delete_message(channel, message, request.user)
        except chat_service.ChatError as exc:
            raise ChatRefused(exc.code, str(exc), exc.status)
        return Response({'ok': True})


class ReadView(_ChatBase):

    def post(self, request, channel_id):
        channel = self.channel(request, channel_id)
        up_to = request.data.get('up_to') if isinstance(request.data, dict) else None
        message = None
        if up_to is not None:
            message = self.message(channel, _int(up_to))
        chat_service.mark_read(channel, request.user, message)
        receipt = ChatReadReceipt.objects.get(user=request.user, channel=channel)
        return Response({'ok': True, 'last_read_message_id': receipt.last_read_message_id})


class NotificationLevelView(_ChatBase):

    def get(self, request, channel_id):
        channel = self.channel(request, channel_id)
        level = (ChatNotificationPreference.objects
                 .filter(user=request.user, channel=channel).values_list('level', flat=True).first())
        return Response({'level': level or ChatNotificationPreference.LEVEL_ALL})

    def post(self, request, channel_id):
        channel = self.channel(request, channel_id)
        level = request.data.get('level') if isinstance(request.data, dict) else None
        valid = {c[0] for c in ChatNotificationPreference.NOTIFICATION_LEVELS}
        if level not in valid:
            raise ChatRefused('invalid_level', f'level must be one of: {", ".join(sorted(valid))}')
        ChatNotificationPreference.objects.update_or_create(
            user=request.user, channel=channel, defaults={'level': level})
        return Response({'level': level})
