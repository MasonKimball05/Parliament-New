from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from src.models import ChatChannel, ChatMessage, ChatReadReceipt, Committee
from src.models_feature_flags import SiteSetting
from src.feature_flag_decorators import require_feature_flag


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

    # Get initial messages (last 50)
    messages = ChatMessage.objects.filter(
        channel=channel,
        is_deleted=False
    ).select_related('sender').order_by('-created_at')[:50]

    messages = reversed(messages)

    # Only update read receipt if user has normal access
    if has_normal_access:
        # Get or create read receipt
        receipt, created = ChatReadReceipt.objects.get_or_create(
            user=request.user,
            channel=channel
        )

        # Update receipt to mark all as read
        if messages:
            latest_message = ChatMessage.objects.filter(
                channel=channel,
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_message:
                receipt.last_read_message = latest_message
                receipt.save()

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

    return render(request, 'chat/channel.html', {
        'channel': channel,
        'initial_messages': messages,
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

    # Get timestamp from query parameter
    since = request.GET.get('since')

    if since:
        # Get messages after this timestamp
        messages = ChatMessage.objects.filter(
            channel=channel,
            created_at__gt=since,
            is_deleted=False
        ).select_related('sender').order_by('created_at')
    else:
        # Get last 50 messages
        messages = ChatMessage.objects.filter(
            channel=channel,
            is_deleted=False
        ).select_related('sender').order_by('-created_at')[:50]
        messages = reversed(messages)

    # Update read receipt - this marks user as active
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    # Must save to update last_read_at timestamp (auto_now=True only updates on save)
    receipt.save()

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

    # Update read receipt for sender
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    receipt.last_read_message = message
    receipt.save()

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
