from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from src.models import ChatChannel, ChatMessage, ChatReadReceipt, Committee
from src.models_feature_flags import SiteSetting, FeatureFlag
from src.feature_flag_decorators import require_feature_flag
from src.models.users import member_defer
from src import chat_service


# v3.56.0: these live in src/chat_service.py (shared with the app); the old
# names stay importable for callers and tests.
_ws_broadcast = chat_service.ws_broadcast
_parse_mentions = chat_service.parse_mentions
_dispatch_chat_push = chat_service.dispatch_push


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
    ).select_related('sender').defer(*member_defer('sender')).order_by('-created_at')

    messages_batch = list(messages_qs[:50])
    has_more_messages = len(messages_batch) == 50
    messages = list(reversed(messages_batch))

    # Only update read receipt if user has normal access
    first_unread_message_id = None
    if has_normal_access:
        from django.core.cache import cache as _cache

        # Get or create read receipt
        receipt, created = ChatReadReceipt.objects.get_or_create(
            user=request.user,
            channel=channel
        )

        # Capture read position BEFORE updating so we can show the "New Messages" divider
        prev_last_read_id = receipt.last_read_message_id

        # Update receipt to mark all as read
        if messages:
            latest_message = messages_qs.first()
            if latest_message:
                receipt.last_read_message = latest_message
                receipt.save(update_fields=['last_read_message'])

        # Find the first message in this page that the user hadn't seen yet
        if prev_last_read_id is not None:
            for msg in messages:
                if msg.id > prev_last_read_id:
                    first_unread_message_id = msg.id
                    break

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

    # Active users poll interval (messages are real-time via WebSocket — no message polling)
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
        'first_unread_message_id': first_unread_message_id,
        'has_more_messages': has_more_messages,
        'is_chair': is_chair,
        'is_vp': is_vp,
        'is_admin': is_admin,
        'admin_preview_mode': admin_preview_mode,
        'can_send_messages': can_send_messages,
        'can_delete_own_messages': can_delete_own_messages,
        'committee_code': committee_code,
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
        try:
            from django.utils.dateparse import parse_datetime
            since_dt = parse_datetime(since)
            if since_dt is None:
                raise ValueError('unparseable')
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid since timestamp'}, status=400)
        messages = ChatMessage.objects.filter(
            channel=channel,
            created_at__gt=since_dt,
            is_deleted=False
        ).select_related('sender').defer(*member_defer('sender')).order_by('created_at')
    elif before:
        # Load more: get messages before this timestamp (older history)
        messages_qs = ChatMessage.objects.filter(
            channel=channel,
            created_at__lt=before,
            is_deleted=False
        ).select_related('sender').defer(*member_defer('sender')).order_by('-created_at')[:50]
        messages = list(reversed(list(messages_qs)))
    else:
        # Initial load fallback
        messages_qs = ChatMessage.objects.filter(
            channel=channel,
            is_deleted=False
        ).select_related('sender').defer(*member_defer('sender')).order_by('-created_at')[:50]
        messages = list(reversed(list(messages_qs)))

    # Update read receipt (marks user as active + invalidates nav badge cache)
    from django.core.cache import cache as _cache
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    # Must save to update last_read_at timestamp (auto_now=True only updates on save)
    receipt.save()

    # For polling requests advance last_read_message so the nav badge clears
    # even when new messages arrive via WebSocket while the user is on the page.
    if since:
        latest_msg = messages.last()
        if latest_msg:
            receipt.last_read_message = latest_msg
            receipt.save(update_fields=['last_read_message'])

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

    # v3.56.0: write check, rate limit, mentions, push and broadcast are
    # chat_service.send_message (shared with the app).
    try:
        message = chat_service.send_message(channel, request.user, request.POST.get('message', ''))
    except chat_service.ChatError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

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

    try:
        chat_service.edit_message(channel, message, request.user, request.POST.get('message', ''))
    except chat_service.ChatError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

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

    # Admins and chairs can delete any message; the sender needs the
    # channel's delete permission (chat_service.can_delete).
    try:
        chat_service.delete_message(channel, message, request.user)
    except chat_service.ChatError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

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
    ).select_related('user').defer(*member_defer('user')).order_by('user__name')

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


@login_required
@require_feature_flag('chats')
def dismiss_chat_unread(request):
    """
    Mark all channels as fully read, clearing the nav badge.
    POST-only. Updates every ChatReadReceipt for this user to point to the
    latest message in each channel, then invalidates the badge cache.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.core.cache import cache as _cache
    from django.db.models import OuterRef, Subquery

    # Single query: annotate each receipt with the latest non-deleted message id
    # in its channel, then bulk-update only the rows that are behind.
    latest_msg_subquery = (
        ChatMessage.objects
        .filter(channel=OuterRef('channel'), is_deleted=False)
        .order_by('-created_at')
        .values('pk')[:1]
    )

    receipts = list(
        ChatReadReceipt.objects
        .filter(user=request.user)
        .annotate(latest_msg_id=Subquery(latest_msg_subquery))
    )

    to_update = []
    for receipt in receipts:
        if receipt.latest_msg_id and receipt.last_read_message_id != receipt.latest_msg_id:
            receipt.last_read_message_id = receipt.latest_msg_id
            to_update.append(receipt)

    if to_update:
        ChatReadReceipt.objects.bulk_update(to_update, ['last_read_message'])

    _cache.delete(f'chat_unread_{request.user.pk}')
    return JsonResponse({'ok': True})
