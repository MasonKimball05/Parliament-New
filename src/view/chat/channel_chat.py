from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.urls import reverse
from django.utils import timezone
from src.models import ChatChannel, ChatMessage, ChatReadReceipt, Committee
from src.models_feature_flags import SiteSetting, FeatureFlag
from src.feature_flag_decorators import require_feature_flag


def _parse_mentions(message_text, channel):
    """
    Parse @username patterns from message text and return a set of matching user PKs.
    Only considers active members.
    """
    import re
    from src.models import ParliamentUser

    raw_mentions = re.findall(r'@([a-zA-Z0-9._]+)', message_text)
    if not raw_mentions:
        return set()

    matched = ParliamentUser.objects.filter(
        username__in=raw_mentions,
        member_status='Active',
    ).values_list('pk', flat=True)
    return set(matched)


def _dispatch_chat_push(channel, message, mentioned_pks=None):
    """
    Fire push notifications to channel members who are not currently viewing the channel.
    Respects per-channel notification preference (all / mentions / none) and the global
    push_chat user preference. Never raises — push failures must not break message send.

    mentioned_pks: set of user PKs explicitly @mentioned in this message.
    """
    try:
        from datetime import timedelta
        from src.models import ChatNotificationPreference
        from src.tasks import send_push_notification

        if mentioned_pks is None:
            mentioned_pks = set()

        sender = message.sender
        active_cutoff = timezone.now() - timedelta(seconds=10)

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
            .select_related('user__preferences')
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

        # Build the channel URL for the push notification tap target
        if channel.channel_type == 'committee' and channel.committee:
            url = reverse('committee_channel_chat', kwargs={'code': channel.committee.code})
        else:
            url = reverse('channel_chat', kwargs={'channel_id': channel.id})

        for receipt in recipients:
            user = receipt.user

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
                title=f'#{channel.name}',
                body=f'{sender.name}: {message.message[:120]}',
                url=url,
                tag=f'chat-{channel.id}',
            )
    except Exception:
        pass  # Never let push failures break message sending


@login_required
@require_feature_flag('chats')
def channel_chat(request, channel_id=None, code=None):
    """Main chat page for a channel (works for all channel types)"""
    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return HttpResponseForbidden("Invalid channel identifier.")

    # Check if user has access to this channel (with admin override)
    has_normal_access = channel.has_access(request.user)
    has_admin_access = request.user.is_admin and channel.has_access(request.user, admin_override=True)

    if not has_normal_access and not has_admin_access:
        return HttpResponseForbidden("You do not have access to this channel.")

    # Get initial messages (last 50), determine whether older ones exist
    messages_qs = ChatMessage.objects.filter(
        channel=channel,
        is_deleted=False
    ).select_related('sender').order_by('-created_at')

    messages_batch = list(messages_qs[:50])
    has_more_messages = len(messages_batch) == 50
    messages = list(reversed(messages_batch))

    # Only update read receipt if user has normal access
    if has_normal_access:
        from django.core.cache import cache as _cache

        # Get or create read receipt
        receipt, created = ChatReadReceipt.objects.get_or_create(
            user=request.user,
            channel=channel
        )

        # Update receipt to mark all as read
        if messages:
            latest_message = messages_qs.first()
            if latest_message:
                receipt.last_read_message = latest_message
                receipt.save()

        # Invalidate the nav unread badge cache
        _cache.delete(f'chat_unread_{request.user.pk}')

    # Determine if user is admin or has special permissions
    is_admin = request.user.is_admin
    is_chair = False
    admin_preview_mode = has_admin_access and not has_normal_access

    # If it's a committee channel, check if user is chair or VP
    is_vp = False
    if channel.channel_type == 'committee' and channel.committee:
        is_chair = channel.committee.is_chair(request.user)
        is_vp = channel.committee.is_vp(request.user)

    # Check if user can send messages
    can_send_messages = channel.can_write(request.user)
    can_delete_own_messages = channel.can_delete_messages(request.user)

    # Determine committee code if this is a committee channel
    committee_code = None
    if channel.channel_type == 'committee' and channel.committee:
        committee_code = channel.committee.code

    # Get chat polling settings from database (with defaults)
    chat_active_poll_interval = SiteSetting.get_setting('chat_active_poll_interval', 3000)
    chat_inactive_poll_interval = SiteSetting.get_setting('chat_inactive_poll_interval', 20000)
    chat_active_users_poll_interval = SiteSetting.get_setting('chat_active_users_poll_interval', 5000)

    # Check if chat feature is enabled (for JavaScript polling control)
    chat_enabled = FeatureFlag.is_feature_enabled('chats')

    # Load user's per-channel notification preference
    from src.models import ChatNotificationPreference
    try:
        notif_pref = ChatNotificationPreference.objects.get(
            user=request.user, channel=channel
        ).level
    except ChatNotificationPreference.DoesNotExist:
        notif_pref = ChatNotificationPreference.LEVEL_ALL

    return render(request, 'chat/channel.html', {
        'channel': channel,
        'initial_messages': messages,
        'has_more_messages': has_more_messages,
        'is_chair': is_chair,
        'is_vp': is_vp,
        'is_admin': is_admin,
        'admin_preview_mode': admin_preview_mode,
        'can_send_messages': can_send_messages,
        'can_delete_own_messages': can_delete_own_messages,
        'committee_code': committee_code,
        'chat_active_poll_interval': chat_active_poll_interval,
        'chat_inactive_poll_interval': chat_inactive_poll_interval,
        'chat_active_users_poll_interval': chat_active_users_poll_interval,
        'chat_enabled': chat_enabled,
        'notif_pref': notif_pref,
        'notif_pref_choices': ChatNotificationPreference.NOTIFICATION_LEVELS,
    })


@login_required
@require_feature_flag('chats')
def get_channel_messages(request, channel_id=None, code=None):
    """API endpoint to poll for new messages"""
    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)

    if not channel.has_access(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    since = request.GET.get('since')
    before = request.GET.get('before')

    if since:
        # Polling: get messages after this timestamp (newest activity)
        messages = ChatMessage.objects.filter(
            channel=channel,
            created_at__gt=since,
            is_deleted=False
        ).select_related('sender').order_by('created_at')
    elif before:
        # Load more: get messages before this timestamp (older history)
        messages_qs = ChatMessage.objects.filter(
            channel=channel,
            created_at__lt=before,
            is_deleted=False
        ).select_related('sender').order_by('-created_at')[:50]
        messages = list(reversed(list(messages_qs)))
    else:
        # Initial load fallback
        messages_qs = ChatMessage.objects.filter(
            channel=channel,
            is_deleted=False
        ).select_related('sender').order_by('-created_at')[:50]
        messages = list(reversed(list(messages_qs)))

    # Update read receipt (marks user as active + invalidates nav badge cache)
    from django.core.cache import cache as _cache
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    # Must save to update last_read_at timestamp (auto_now=True only updates on save)
    receipt.save()
    _cache.delete(f'chat_unread_{request.user.pk}')

    messages_data = [{
        'id': msg.id,
        'sender_id': msg.sender.user_id,
        'sender_name': msg.sender.name,
        'sender_profile_picture': msg.sender.profile_picture.url if msg.sender.profile_picture else None,
        'message': msg.message,
        'created_at': msg.created_at.isoformat(),
        'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
        'is_own_message': msg.sender == request.user
    } for msg in messages]

    return JsonResponse({'messages': messages_data})


@login_required
@require_feature_flag('chats')
def send_channel_message(request, channel_id=None, code=None):
    """API endpoint to send a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)

    # Check if user has write permission
    if not channel.can_write(request.user):
        return JsonResponse({'error': 'You do not have permission to send messages in this channel'}, status=403)

    message_text = request.POST.get('message', '').strip()

    if not message_text:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)

    if len(message_text) > 2000:
        return JsonResponse({'error': 'Message too long (max 2000 characters)'}, status=400)

    # Create message
    message = ChatMessage.objects.create(
        channel=channel,
        sender=request.user,
        message=message_text
    )

    # Update read receipt for sender and invalidate their nav badge cache
    from django.core.cache import cache as _cache
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    receipt.last_read_message = message
    receipt.save()
    _cache.delete(f'chat_unread_{request.user.pk}')

    # Parse @mentions and dispatch targeted push notifications
    mentioned_pks = _parse_mentions(message_text, channel)
    _dispatch_chat_push(channel, message, mentioned_pks=mentioned_pks)

    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'sender_id': message.sender.user_id,
            'sender_name': message.sender.name,
            'sender_profile_picture': message.sender.profile_picture.url if message.sender.profile_picture else None,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_own_message': True
        }
    })


@login_required
@require_feature_flag('chats')
def edit_channel_message(request, message_id, channel_id=None, code=None):
    """API endpoint to edit a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)
    message = get_object_or_404(ChatMessage, id=message_id, channel=channel)

    # Only the sender can edit their message
    if message.sender != request.user:
        return JsonResponse({'error': 'Only the sender can edit this message'}, status=403)

    # Check if message is within 1 hour edit window
    from datetime import timedelta
    time_since_creation = timezone.now() - message.created_at
    if time_since_creation > timedelta(hours=1):
        return JsonResponse({'error': 'Messages can only be edited within 1 hour of sending'}, status=403)

    new_message_text = request.POST.get('message', '').strip()

    if not new_message_text:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)

    if len(new_message_text) > 2000:
        return JsonResponse({'error': 'Message too long (max 2000 characters)'}, status=400)

    # Update message
    message.message = new_message_text
    message.edited_at = timezone.now()
    message.save()

    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'message': message.message,
            'edited_at': message.edited_at.isoformat()
        }
    })


@login_required
@require_feature_flag('chats')
def delete_channel_message(request, message_id, channel_id=None, code=None):
    """API endpoint to delete a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)
    message = get_object_or_404(ChatMessage, id=message_id, channel=channel)

    # Check permissions: admin, chairs can delete any message
    # Message sender can delete own message if they have delete permission
    is_sender = message.sender == request.user
    is_admin = request.user.is_admin
    is_chair = channel.committee and channel.committee.is_chair(request.user)

    can_delete = (
        is_admin or
        is_chair or
        (is_sender and channel.can_delete_messages(request.user))
    )

    if not can_delete:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Soft delete
    message.is_deleted = True
    message.save()

    return JsonResponse({'success': True})


@login_required
@require_feature_flag('chats')
def get_channel_active_users(request, channel_id=None, code=None):
    """API endpoint to get list of users currently active in channel"""
    # Look up channel by committee code or channel ID
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)

    if not channel.has_access(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    from datetime import timedelta

    # Consider users active if they've polled in the last 10 seconds
    cutoff_time = timezone.now() - timedelta(seconds=10)

    active_receipts = ChatReadReceipt.objects.filter(
        channel=channel,
        last_read_at__gte=cutoff_time
    ).select_related('user').order_by('user__name')

    active_users = [{
        'user_id': receipt.user.user_id,
        'name': receipt.user.name,
        'is_current_user': receipt.user == request.user
    } for receipt in active_receipts]

    return JsonResponse({
        'active_users': active_users,
        'count': len(active_users)
    })

@login_required
@require_feature_flag('chats')
def get_channel_members(request, channel_id=None, code=None):
    """API endpoint that returns mentionable members for @mention autocomplete."""
    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)

    if not channel.has_access(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    from src.models import ParliamentUser, ChatChannelPermission

    if channel.access_type == 'open':
        users = ParliamentUser.objects.filter(
            member_status='Active'
        ).order_by('name').values('user_id', 'name', 'username')
    elif channel.access_type == 'committee' and channel.committee:
        users = channel.committee.members.filter(
            member_status='Active'
        ).order_by('name').values('user_id', 'name', 'username')
    else:
        # Restricted: users with explicit permission
        user_ids = ChatChannelPermission.objects.filter(
            channel=channel,
            user__isnull=False,
            can_read=True,
        ).values_list('user_id', flat=True)
        users = ParliamentUser.objects.filter(
            pk__in=user_ids
        ).order_by('name').values('user_id', 'name', 'username')

    members = [
        {'id': u['user_id'], 'name': u['name'], 'username': u['username']}
        for u in users
    ]
    return JsonResponse({'members': members})


@login_required
@require_feature_flag('chats')
def set_channel_notification_pref(request, channel_id=None, code=None):
    """POST endpoint to set the user's per-channel notification preference."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if code:
        committee = get_object_or_404(Committee, code=code)
        channel = get_object_or_404(ChatChannel, committee=committee, channel_type='committee')
    elif channel_id:
        channel = get_object_or_404(ChatChannel, id=channel_id)
    else:
        return JsonResponse({'error': 'Invalid channel identifier'}, status=400)

    if not channel.has_access(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    import json
    from src.models import ChatNotificationPreference

    try:
        body = json.loads(request.body)
        level = body.get('level', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    valid_levels = {c[0] for c in ChatNotificationPreference.NOTIFICATION_LEVELS}
    if level not in valid_levels:
        return JsonResponse({'error': f'Invalid level. Must be one of: {", ".join(valid_levels)}'}, status=400)

    ChatNotificationPreference.objects.update_or_create(
        user=request.user,
        channel=channel,
        defaults={'level': level},
    )

    return JsonResponse({'success': True, 'level': level})
