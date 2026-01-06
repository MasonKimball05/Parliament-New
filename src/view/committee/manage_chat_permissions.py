from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from src.models import Committee, ChatChannel, ChatChannelPermission, ParliamentUser


@login_required
def manage_chat_permissions(request, code):
    """Allow committee chair/admin to manage guest chat permissions"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair, VP, or admin
    is_chair = committee.is_chair(request.user)
    is_vp = committee.is_vp(request.user)
    is_admin = request.user.is_admin

    if not (is_chair or is_vp or is_admin):
        messages.error(request, 'Only committee chairs and admins can manage chat permissions')
        return redirect('committee_detail', code=code)

    # Get the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        messages.error(request, 'Chat channel not found for this committee')
        return redirect('committee_detail', code=code)

    # Get existing guest permissions (users who are NOT committee members)
    committee_member_ids = list(committee.members.values_list('user_id', flat=True))
    guest_permissions = ChatChannelPermission.objects.filter(
        channel=channel,
        user__isnull=False
    ).exclude(
        user__user_id__in=committee_member_ids
    ).select_related('user').order_by('user__name')

    # Get all active users who are not committee members (for adding new guests)
    available_users = ParliamentUser.active.exclude(
        user_id__in=committee_member_ids
    ).order_by('name')

    context = {
        'committee': committee,
        'channel': channel,
        'guest_permissions': guest_permissions,
        'available_users': available_users,
        'is_chair': is_chair,
        'is_vp': is_vp,
    }

    return render(request, 'committee/manage_chat_permissions.html', context)


@login_required
@require_http_methods(["POST"])
def add_guest_permission(request, code):
    """Add a guest user to the committee chat with specific permissions"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair, VP, or admin
    if not (committee.is_chair(request.user) or committee.is_vp(request.user) or request.user.is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    user_id = request.POST.get('user_id')
    can_read = request.POST.get('can_read') == 'true'
    can_write = request.POST.get('can_write') == 'true'
    can_delete = request.POST.get('can_delete') == 'true'

    if not user_id:
        return JsonResponse({'error': 'User ID is required'}, status=400)

    # Get the user
    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    # Make sure user is not already a committee member
    if committee.is_member(guest_user):
        return JsonResponse({'error': 'This user is already a committee member'}, status=400)

    # Check if permission already exists
    existing_perm = ChatChannelPermission.objects.filter(
        channel=channel,
        user=guest_user
    ).first()

    if existing_perm:
        # Update existing permission
        existing_perm.can_read = can_read
        existing_perm.can_write = can_write
        existing_perm.can_delete = can_delete
        existing_perm.save()
        action = 'updated'
    else:
        # Create new permission
        ChatChannelPermission.objects.create(
            channel=channel,
            user=guest_user,
            can_read=can_read,
            can_write=can_write,
            can_delete=can_delete
        )
        action = 'added'

    return JsonResponse({
        'success': True,
        'message': f'Guest permission {action} for {guest_user.name}',
        'user_name': guest_user.name,
        'user_id': guest_user.user_id,
        'can_read': can_read,
        'can_write': can_write,
        'can_delete': can_delete
    })


@login_required
@require_http_methods(["POST"])
def update_guest_permission(request, code, user_id):
    """Update guest user's chat permissions"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair, VP, or admin
    if not (committee.is_chair(request.user) or committee.is_vp(request.user) or request.user.is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    # Get the guest user
    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    # Get the permission
    try:
        permission = ChatChannelPermission.objects.get(
            channel=channel,
            user=guest_user
        )
    except ChatChannelPermission.DoesNotExist:
        return JsonResponse({'error': 'Permission not found'}, status=404)

    # Update permissions
    can_read = request.POST.get('can_read') == 'true'
    can_write = request.POST.get('can_write') == 'true'
    can_delete = request.POST.get('can_delete') == 'true'

    permission.can_read = can_read
    permission.can_write = can_write
    permission.can_delete = can_delete
    permission.save()

    return JsonResponse({
        'success': True,
        'message': f'Updated permissions for {guest_user.name}',
        'can_read': can_read,
        'can_write': can_write,
        'can_delete': can_delete
    })


@login_required
@require_http_methods(["POST"])
def remove_guest_permission(request, code, user_id):
    """Remove a guest user's chat permissions"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair, VP, or admin
    if not (committee.is_chair(request.user) or committee.is_vp(request.user) or request.user.is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    # Get the guest user
    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    # Remove the permission
    deleted_count, _ = ChatChannelPermission.objects.filter(
        channel=channel,
        user=guest_user
    ).delete()

    if deleted_count > 0:
        return JsonResponse({
            'success': True,
            'message': f'Removed {guest_user.name} from guest list'
        })
    else:
        return JsonResponse({'error': 'Permission not found'}, status=404)
