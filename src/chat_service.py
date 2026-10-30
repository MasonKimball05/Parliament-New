"""
Chat actions: send, edit, delete, read (v4.0.0).

The one implementation behind the website's chat endpoints
(``src/view/chat/channel_chat.py``) and the app's ``chat/`` endpoints
(``src/mobile/views_chat.py``). Moved here from the web views unchanged, so
the write rule, the rate limit, @mention privacy, the push fan-out (with its
hidden text for private channels) and the websocket broadcast can't drift.

Who may read a channel is NOT decided here: that is ``ChatChannel`` (the one
access rule, ``_has_access_cached``). Callers check read access before
calling; ``send_message`` re-checks ``can_write``, edit and delete apply their
own sender/permission rules as the web did.

Refusals raise ``ChatError`` (``.code``, ``.status``, ``str()`` = the web's
message).
"""
import re
from datetime import timedelta

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from src.models import ChatMessage, ChatReadReceipt
from src.models.users import member_defer

MAX_LENGTH = 2000
EDIT_WINDOW = timedelta(hours=1)
#: Sends per user per RATE_WINDOW seconds.
RATE_LIMIT, RATE_WINDOW = 5, 3
#: A member whose client touched the channel this recently is "active" and
#: isn't pushed (they can see it live).
ACTIVE_WINDOW = timedelta(seconds=10)


class ChatError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def channel_url(channel):
    if channel.channel_type == 'committee' and channel.committee:
        return reverse('committee_channel_chat', kwargs={'code': channel.committee.code})
    return reverse('channel_chat', kwargs={'channel_id': channel.id})


def serialize(msg, viewer):
    """The web chat's message JSON (``get_channel_messages``)."""
    return {
        'id': msg.id,
        'sender_id': msg.sender.user_id,
        'sender_name': msg.sender.name,
        'sender_profile_picture': msg.sender.profile_picture.url if msg.sender.profile_picture else None,
        'message': msg.message,
        'created_at': msg.created_at.isoformat(),
        'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
        'is_own_message': msg.sender_id == viewer.pk,
    }


def messages_qs(channel):
    return (ChatMessage.objects.filter(channel=channel, is_deleted=False)
            .select_related('sender').defer(*member_defer('sender')))


def _invalidate_badge(user):
    cache.delete(f'chat_unread_{user.pk}')


def touch_presence(channel, user):
    """Mark ``user`` active in ``channel`` (``last_read_at`` is auto_now)."""
    receipt, _ = ChatReadReceipt.objects.get_or_create(user=user, channel=channel)
    receipt.save()
    return receipt


def mark_read(channel, user, message=None):
    """Advance the read marker to ``message`` (default: the latest)."""
    message = message or messages_qs(channel).order_by('-created_at').first()
    receipt, _ = ChatReadReceipt.objects.get_or_create(user=user, channel=channel)
    if message is not None and receipt.last_read_message_id != message.id:
        receipt.last_read_message = message
        receipt.save(update_fields=['last_read_message'])
    _invalidate_badge(user)


def ws_broadcast(group_name, payload):
    """
    Send a message to a Channels group. Silently no-ops if the channel layer
    is not configured (e.g., running without Redis in a bare dev environment).
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer:
            async_to_sync(layer.group_send)(group_name, payload)
    except Exception:
        pass  # Never let a WS broadcast error break the HTTP response


def parse_mentions(message_text, channel):
    """
    Parse @username patterns from message text and return a set of matching user PKs.
    Only considers active members who can read `channel` — a mention creates a
    Notification carrying the message text, so naming someone outside a private
    channel must not reach them.
    """
    from src.models import ParliamentUser

    raw_mentions = re.findall(r'@([a-zA-Z0-9._]+)', message_text)
    if not raw_mentions:
        return set()

    candidates = ParliamentUser.objects.filter(
        username__in=raw_mentions,
        member_status='Active',
    )
    return {u.pk for u in candidates if channel.has_access(u)}


def dispatch_push(channel, message, mentioned_pks=None):
    """
    Fire push notifications to channel members who are not currently viewing the channel.
    Respects per-channel notification preference (all / mentions / none) and the global
    push_chat user preference. Never raises — push failures must not break message send.

    mentioned_pks: set of user PKs explicitly @mentioned in this message.

    Returns the set of user PKs a push was queued for, so the mention
    notification doesn't push the same person a second time.
    """
    pushed = set()
    try:
        from src.models import ChatNotificationPreference
        from src.tasks import send_push_notification

        if mentioned_pks is None:
            mentioned_pks = set()

        sender = message.sender
        active_cutoff = timezone.now() - ACTIVE_WINDOW

        # IDs of users currently active in the channel (they can see it live)
        active_ids = set(
            ChatReadReceipt.objects.filter(
                channel=channel,
                last_read_at__gte=active_cutoff,
            ).values_list('user_id', flat=True)
        )
        active_ids.add(sender.pk)

        # Users who have previously visited this channel and are not active right now
        recipients = list(
            ChatReadReceipt.objects
            .filter(channel=channel)
            .exclude(user_id__in=active_ids)
            .select_related('user__preferences').defer(*member_defer('user'))
        )

        # Load per-channel prefs for all candidate recipients in one query
        recipient_pks = [r.user_id for r in recipients]
        pref_map = {
            p.user_id: p.level
            for p in ChatNotificationPreference.objects.filter(
                channel=channel,
                user_id__in=recipient_pks,
            )
        }

        url = channel_url(channel)

        # Channel names and text can be sensitive (Kai): only open channels
        # put them on a lock screen.
        if channel.access_type == 'open':
            title = f'#{channel.name}'
            body = f'{sender.name}: {message.message[:120]}'
        else:
            title = 'Parliament'
            body = 'New message in a private channel'

        for receipt in recipients:
            user = receipt.user

            # A read receipt outlives access (expired guest, left committee).
            if not channel.has_access(user):
                continue

            # Check global push_chat preference
            prefs = getattr(user, 'preferences', None)
            if prefs is not None and not prefs.push_chat:
                continue

            # Check per-channel notification level (default: 'all')
            level = pref_map.get(user.pk, ChatNotificationPreference.LEVEL_ALL)
            if level == ChatNotificationPreference.LEVEL_NONE:
                continue
            if level == ChatNotificationPreference.LEVEL_MENTIONS and user.pk not in mentioned_pks:
                continue

            send_push_notification.delay(
                user_id=user.pk,
                title=title,
                body=body,
                url=url,
                tag=f'chat-{channel.id}',
            )
            pushed.add(user.pk)
    except Exception:
        pass  # Never let push failures break message sending
    return pushed


def _clean_text(text):
    text = (text or '').strip() if isinstance(text, str) else ''
    if not text:
        raise ChatError('empty', 'Message cannot be empty')
    if len(text) > MAX_LENGTH:
        raise ChatError('too_long', f'Message too long (max {MAX_LENGTH} characters)')
    return text


def send_message(channel, user, text):
    """Post ``text`` to ``channel`` as ``user``. Returns the ChatMessage."""
    if not channel.can_write(user):
        raise ChatError('forbidden', 'You do not have permission to send messages in this channel', 403)

    # Rate limit: max 5 messages per 3 seconds per user
    rate_key = f'chat_send_rate_{user.pk}'
    sent = cache.get(rate_key, 0)
    if sent >= RATE_LIMIT:
        raise ChatError('rate_limited', 'You are sending messages too quickly. Please slow down.', 429)
    cache.set(rate_key, sent + 1, RATE_WINDOW)

    text = _clean_text(text)
    message = ChatMessage.objects.create(channel=channel, sender=user, message=text)

    # The sender has read their own message.
    mark_read(channel, user, message)

    # Parse @mentions and dispatch targeted push notifications
    mentioned_pks = parse_mentions(text, channel)
    chat_pushed_pks = dispatch_push(channel, message, mentioned_pks=mentioned_pks)

    # Create in-app mention notifications and collect user_ids for WS broadcast
    mentioned_user_ids = []
    if mentioned_pks:
        from src.models import ParliamentUser
        from src.notification_service import create_notification

        url = channel_url(channel)
        is_open = channel.access_type == 'open'
        for mentioned in ParliamentUser.objects.filter(pk__in=mentioned_pks):
            mentioned_user_ids.append(str(mentioned.user_id))
            if mentioned.pk == user.pk:
                continue  # Don't self-notify
            # Through the service so the mention also pushes (prefs['push']
            # ['mentions']) — unless the chat fan-out above already pushed
            # this person for this message. A private channel's name and
            # text stay off the lock screen, as in dispatch_push.
            create_notification(
                recipient=mentioned,
                notification_type='chat_mention',
                title=f'{user.name} mentioned you in #{channel.name}',
                message=text[:200],
                link=url,
                source_type='chatmessage',
                source_id=message.id,
                push=mentioned.pk not in chat_pushed_pks,
                push_title=None if is_open else 'Parliament',
                push_body=None if is_open else f'{user.name} mentioned you in a private channel',
            )

    # Broadcast to WebSocket group so all connected clients receive it in real-time
    ws_broadcast(f'chat_{channel.id}', {
        'type': 'chat.message',
        'message': {
            **{k: v for k, v in serialize(message, user).items() if k != 'is_own_message'},
            'mentioned_user_ids': mentioned_user_ids,
        },
    })
    return message


def channel_permissions(channel, user):
    """
    The member's edit/delete standing in ``channel``, fetched once. Pass it to
    can_edit / can_delete to flag a whole page of messages without a query
    per message; the rule itself is still ChatChannel's.
    """
    return {
        'edit': channel.can_edit_messages(user),
        'delete': channel.can_delete_messages(user),
        'chair': bool(channel.committee and channel.committee.is_chair(user)),
        'admin': bool(user.is_admin),
    }


def can_edit(channel, message, user, perms=None):
    """Sender only, within the edit window, with the channel's edit permission."""
    if message.sender_id != user.pk or timezone.now() - message.created_at > EDIT_WINDOW:
        return False
    return (perms or channel_permissions(channel, user))['edit']


def edit_message(channel, message, user, text):
    # Only the sender can edit their message
    if message.sender_id != user.pk:
        raise ChatError('forbidden', 'Only the sender can edit this message', 403)
    # Committee members always pass; guests need can_edit=True
    if not channel.can_edit_messages(user):
        raise ChatError('forbidden', 'You do not have permission to edit messages in this channel', 403)
    if timezone.now() - message.created_at > EDIT_WINDOW:
        raise ChatError('edit_window_passed', 'Messages can only be edited within 1 hour of sending', 403)
    message.message = _clean_text(text)
    message.edited_at = timezone.now()
    message.save(update_fields=['message', 'edited_at'])
    ws_broadcast(f'chat_{channel.id}', {
        'type': 'chat.edit',
        'message': {
            'id': message.id,
            'message': message.message,
            'edited_at': message.edited_at.isoformat(),
        },
    })
    return message


def can_delete(channel, message, user, perms=None):
    """Admins and committee chairs delete anything; senders need the permission."""
    perms = perms or channel_permissions(channel, user)
    return bool(perms['admin'] or perms['chair']
                or (message.sender_id == user.pk and perms['delete']))


def delete_message(channel, message, user):
    if not can_delete(channel, message, user):
        raise ChatError('forbidden', 'Permission denied', 403)
    # Soft delete
    message.is_deleted = True
    message.save(update_fields=['is_deleted'])
    ws_broadcast(f'chat_{channel.id}', {'type': 'chat.delete', 'message_id': message.id})
