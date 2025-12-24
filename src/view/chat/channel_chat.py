from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from src.models import ChatChannel, ChatMessage, ChatReadReceipt


@login_required
def channel_chat(request, channel_id):
    """Main chat page for a channel (works for all channel types)"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

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

    # If it's a committee channel, check if user is chair
    if channel.channel_type == 'committee' and channel.committee:
        is_chair = channel.committee.is_chair(request.user)

    return render(request, 'chat/channel.html', {
        'channel': channel,
        'initial_messages': messages,
        'is_chair': is_chair,
        'is_admin': is_admin,
        'admin_preview_mode': admin_preview_mode,
    })


@login_required
def get_channel_messages(request, channel_id):
    """API endpoint to poll for new messages"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

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
        'message': msg.message,
        'created_at': msg.created_at.isoformat(),
        'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
        'is_own_message': msg.sender == request.user
    } for msg in messages]

    return JsonResponse({'messages': messages_data})


@login_required
def send_channel_message(request, channel_id):
    """API endpoint to send a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    channel = get_object_or_404(ChatChannel, id=channel_id)

    # Must have normal access, not just admin override
    if not channel.has_access(request.user, admin_override=False):
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
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_own_message': True
        }
    })


@login_required
def edit_channel_message(request, channel_id, message_id):
    """API endpoint to edit a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    channel = get_object_or_404(ChatChannel, id=channel_id)
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
def delete_channel_message(request, channel_id, message_id):
    """API endpoint to delete a message"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    channel = get_object_or_404(ChatChannel, id=channel_id)
    message = get_object_or_404(ChatMessage, id=message_id, channel=channel)

    # Check permissions: admin, channel creator, or message sender can delete
    can_delete = (
        request.user.is_admin or
        message.sender == request.user or
        (channel.committee and channel.committee.is_chair(request.user))
    )

    if not can_delete:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Soft delete
    message.is_deleted = True
    message.save()

    return JsonResponse({'success': True})


@login_required
def get_channel_active_users(request, channel_id):
    """API endpoint to get list of users currently active in channel"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

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
