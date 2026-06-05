from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from src.models import Committee, ChatChannel, ChatChannelPermission, ParliamentUser


def _check_chair_access(request, committee):
    """Return (is_chair, is_vp, is_admin) or JsonResponse 403 if unauthorized."""
    is_chair = committee.is_chair(request.user)
    is_vp = committee.is_vp(request.user)
    is_admin = request.user.is_admin
    return is_chair, is_vp, is_admin


def _get_channel(committee):
    """Return committee ChatChannel or None."""
    try:
        return ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        return None


def _serialize_perm(perm):
    return {
        'user_id': perm.user.user_id,
        'user_name': perm.user.name,
        'member_type': perm.user.member_type,
        'member_status': perm.user.member_status,
        'can_read': perm.can_read,
        'can_write': perm.can_write,
        'can_delete': perm.can_delete,
        'can_edit': perm.can_edit,
        'expires_at': perm.expires_at.isoformat() if perm.expires_at else None,
        'is_expired': perm.is_expired,
    }


@login_required
def manage_chat_permissions(request, code):
    """Allow committee chair/admin to manage guest chat permissions."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        messages.error(request, 'Only committee chairs and admins can manage chat permissions')
        return redirect('committee_home', code=code)

    channel = _get_channel(committee)
    if not channel:
        messages.error(request, 'Chat channel not found for this committee')
        return redirect('committee_home', code=code)

    committee_member_ids = list(committee.members.values_list('user_id', flat=True))

    # Guest permissions: user-specific perms for non-members (includes alumni)
    guest_permissions = ChatChannelPermission.objects.filter(
        channel=channel,
        user__isnull=False
    ).exclude(
        user__user_id__in=committee_member_ids
    ).select_related('user').order_by('user__member_status', 'user__name')

    # Available users: active + alumni, excluding committee members
    available_users = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Alumni']
    ).exclude(
        user_id__in=committee_member_ids
    ).order_by('member_status', 'name')

    already_added_ids = set(guest_permissions.values_list('user__user_id', flat=True))

    context = {
        'committee': committee,
        'channel': channel,
        'guest_permissions': guest_permissions,
        'available_users': available_users,
        'already_added_ids': already_added_ids,
        'is_chair': is_chair,
        'is_vp': is_vp,
    }

    return render(request, 'committee/manage_chat_permissions.html', context)


@login_required
@require_http_methods(["POST"])
def add_guest_permission(request, code):
    """Add a guest user to the committee chat with specific permissions."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    channel = _get_channel(committee)
    if not channel:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    user_id = request.POST.get('user_id')
    can_read = request.POST.get('can_read') == 'true'
    can_write = request.POST.get('can_write') == 'true'
    can_delete = request.POST.get('can_delete') == 'true'
    can_edit = request.POST.get('can_edit') == 'true'
    expires_at_raw = request.POST.get('expires_at', '').strip()

    if not user_id:
        return JsonResponse({'error': 'User ID is required'}, status=400)

    if not can_read and not can_write:
        return JsonResponse({'error': 'Guest must have at least read or write permission'}, status=400)

    # Parse optional expiry date
    expires_at = None
    if expires_at_raw:
        try:
            from datetime import datetime
            expires_at = timezone.make_aware(datetime.fromisoformat(expires_at_raw))
            if expires_at <= timezone.now():
                return JsonResponse({'error': 'Expiry date must be in the future'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid expiry date format'}, status=400)

    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    if committee.is_member(guest_user):
        return JsonResponse({'error': 'This user is already a committee member'}, status=400)

    perm, created = ChatChannelPermission.objects.update_or_create(
        channel=channel,
        user=guest_user,
        defaults={
            'can_read': can_read,
            'can_write': can_write,
            'can_delete': can_delete,
            'can_edit': can_edit,
            'expires_at': expires_at,
        }
    )

    return JsonResponse({
        'success': True,
        'message': f'Guest permission {"added" if created else "updated"} for {guest_user.name}',
        **_serialize_perm(perm),
    })


@login_required
@require_http_methods(["POST"])
def bulk_add_guest_permissions(request, code):
    """Add multiple guests at once with the same permission level."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    channel = _get_channel(committee)
    if not channel:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    user_ids = request.POST.getlist('user_ids')
    can_read = request.POST.get('can_read') == 'true'
    can_write = request.POST.get('can_write') == 'true'
    can_delete = request.POST.get('can_delete') == 'true'
    can_edit = request.POST.get('can_edit') == 'true'
    expires_at_raw = request.POST.get('expires_at', '').strip()

    if not user_ids:
        return JsonResponse({'error': 'No users selected'}, status=400)

    if not can_read and not can_write:
        return JsonResponse({'error': 'Must grant at least read or write permission'}, status=400)

    # Parse optional expiry date
    expires_at = None
    if expires_at_raw:
        try:
            from datetime import datetime
            expires_at = timezone.make_aware(datetime.fromisoformat(expires_at_raw))
            if expires_at <= timezone.now():
                return JsonResponse({'error': 'Expiry date must be in the future'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid expiry date format'}, status=400)

    committee_member_ids = set(committee.members.values_list('user_id', flat=True))
    added, skipped = [], []

    for user_id in user_ids:
        if user_id in committee_member_ids:
            skipped.append(user_id)
            continue
        try:
            guest_user = ParliamentUser.objects.get(user_id=user_id)
        except ParliamentUser.DoesNotExist:
            skipped.append(user_id)
            continue

        ChatChannelPermission.objects.update_or_create(
            channel=channel,
            user=guest_user,
            defaults={
                'can_read': can_read,
                'can_write': can_write,
                'can_delete': can_delete,
                'can_edit': can_edit,
                'expires_at': expires_at,
            }
        )
        added.append(guest_user.name)

    return JsonResponse({
        'success': True,
        'added': added,
        'skipped': skipped,
        'message': f'Added {len(added)} guest(s)' + (f', skipped {len(skipped)}' if skipped else ''),
    })


@login_required
@require_http_methods(["POST"])
def update_guest_permission(request, code, user_id):
    """Update a guest user's chat permissions."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    channel = _get_channel(committee)
    if not channel:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    try:
        permission = ChatChannelPermission.objects.get(channel=channel, user=guest_user)
    except ChatChannelPermission.DoesNotExist:
        return JsonResponse({'error': 'Permission not found'}, status=404)

    can_read = request.POST.get('can_read') == 'true'
    can_write = request.POST.get('can_write') == 'true'
    can_delete = request.POST.get('can_delete') == 'true'
    can_edit = request.POST.get('can_edit') == 'true'
    expires_at_raw = request.POST.get('expires_at', '').strip()

    if not can_read and not can_write:
        return JsonResponse({'error': 'Guest must retain at least read or write permission'}, status=400)

    # Parse optional expiry date (empty string clears it)
    if expires_at_raw:
        try:
            from datetime import datetime
            expires_at = timezone.make_aware(datetime.fromisoformat(expires_at_raw))
            if expires_at <= timezone.now():
                return JsonResponse({'error': 'Expiry date must be in the future'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid expiry date format'}, status=400)
    else:
        expires_at = None

    permission.can_read = can_read
    permission.can_write = can_write
    permission.can_delete = can_delete
    permission.can_edit = can_edit
    permission.expires_at = expires_at
    permission.save()

    return JsonResponse({
        'success': True,
        'message': f'Updated permissions for {guest_user.name}',
        'can_read': can_read,
        'can_write': can_write,
        'can_delete': can_delete,
        'can_edit': can_edit,
        'expires_at': permission.expires_at.isoformat() if permission.expires_at else None,
    })


@login_required
@require_http_methods(["POST"])
def remove_guest_permission(request, code, user_id):
    """Remove a guest user's chat permissions."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    channel = _get_channel(committee)
    if not channel:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    deleted_count, _ = ChatChannelPermission.objects.filter(
        channel=channel,
        user=guest_user
    ).delete()

    if deleted_count > 0:
        return JsonResponse({'success': True, 'message': f'Removed {guest_user.name} from guest list'})
    return JsonResponse({'error': 'Permission not found'}, status=404)


@login_required
@require_http_methods(["POST"])
def bulk_remove_guest_permissions(request, code):
    """Remove multiple guests at once."""
    committee = get_object_or_404(Committee, code=code)
    is_chair, is_vp, is_admin = _check_chair_access(request, committee)

    if not (is_chair or is_vp or is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    channel = _get_channel(committee)
    if not channel:
        return JsonResponse({'error': 'Chat channel not found'}, status=404)

    user_ids = request.POST.getlist('user_ids')
    if not user_ids:
        return JsonResponse({'error': 'No users selected'}, status=400)

    deleted_count, _ = ChatChannelPermission.objects.filter(
        channel=channel,
        user__user_id__in=user_ids
    ).delete()

    return JsonResponse({
        'success': True,
        'message': f'Removed {deleted_count} guest(s)',
        'removed_ids': user_ids,
    })
