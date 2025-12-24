"""
Committee chat views - Redirect to channel-based chat
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_http_methods
from src.models import Committee, ChatMessage, ChatReadReceipt, ChatChannel
from django.utils import timezone


@login_required
def committee_chat(request, code):
    """Redirect to channel-based chat for this committee"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is member of committee
    if not committee.is_member(request.user) and not request.user.is_admin:
        return HttpResponseForbidden("You must be a member of this committee to access chat.")

    # Get the chat channel for this committee
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
        return redirect('channel_chat', channel_id=channel.id)
    except ChatChannel.DoesNotExist:
        return HttpResponseForbidden("Chat channel not found for this committee.")


@login_required
def get_chat_messages(request, code):
    """API endpoint to get new messages - redirects to channel-based API"""
    committee = get_object_or_404(Committee, code=code)

    # Get the chat channel for this committee
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    # Check membership
    if not committee.is_member(request.user) and not request.user.is_admin:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    # Update user's last activity in this chat
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        channel=channel
    )
    receipt.last_read_at = timezone.now()
    receipt.save()

    # Get 'since' parameter (timestamp of last message)
    since = request.GET.get('since')

    if since:
        messages = ChatMessage.objects.filter(
            channel=channel,
            created_at__gt=since,
            is_deleted=False
        ).select_related('sender').order_by('created_at')
    else:
        # Return last 50 messages if no timestamp provided
        messages = ChatMessage.objects.filter(
            channel=channel,
            is_deleted=False
        ).select_related('sender').order_by('-created_at')[:50]
        messages = reversed(messages)

    data = [{
        'id': msg.id,
        'sender_name': msg.sender.name,
        'sender_id': msg.sender.user_id,
        'message': msg.message,
        'created_at': msg.created_at.isoformat(),
        'is_own_message': msg.sender == request.user,
    } for msg in messages]

    return JsonResponse({'messages': data})


@login_required
@require_http_methods(["POST"])
def send_chat_message(request, code):
    """API endpoint to send a new message - uses channel system"""
    committee = get_object_or_404(Committee, code=code)

    # Get the chat channel for this committee
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    # Check membership
    if not committee.is_member(request.user) and not request.user.is_admin:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    message_text = request.POST.get('message', '').strip()

    if not message_text:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)

    if len(message_text) > 2000:
        return JsonResponse({'error': 'Message too long (max 2000 characters)'}, status=400)

    # Create the message with channel
    message = ChatMessage.objects.create(
        channel=channel,
        committee=committee,  # Keep for backward compatibility
        sender=request.user,
        message=message_text
    )

    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'sender_name': message.sender.name,
            'sender_id': message.sender.user_id,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_own_message': True,
        }
    })


@login_required
@require_http_methods(["POST"])
def delete_chat_message(request, code, message_id):
    """API endpoint to delete a message (soft delete) - uses channel system"""
    committee = get_object_or_404(Committee, code=code)

    # Get the chat channel for this committee
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    message = get_object_or_404(ChatMessage, id=message_id, channel=channel)

    # Only sender, chair, or admin can delete
    is_sender = message.sender == request.user
    is_chair = committee.is_chair(request.user)
    is_admin = request.user.is_admin

    if not (is_sender or is_chair or is_admin):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    message.is_deleted = True
    message.save()

    return JsonResponse({'success': True})


@login_required
def get_active_users(request, code):
    """API endpoint to get list of users currently active in chat - uses channel system"""
    committee = get_object_or_404(Committee, code=code)

    # Get the chat channel for this committee
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    # Check membership
    if not committee.is_member(request.user) and not request.user.is_admin:
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
