from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from src.models import Committee, ChatChannel, ChatChannelPermission, ParliamentUser
from src.models.users import member_defer

#: A guest permission's four switches.
GUEST_FLAGS = ('can_read', 'can_write', 'can_delete', 'can_edit')


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


# ── Shared with the app (src/mobile/views_committee_settings.py) ─────────────

def can_manage_chat_permissions(user, committee):
    """These pages' rule: the committee's chairs, its VP and admins."""
    return bool(committee.is_chair(user) or committee.is_vp(user) or user.is_admin)


def committee_chat_channel(committee):
    """The committee's chat channel, or None (the pages refuse without one)."""
    return _get_channel(committee)


def chat_guest_permissions(committee, channel):
    """Guest permissions: user-specific perms for non-members (includes alumni)."""
    committee_member_ids = list(committee.members.values_list('user_id', flat=True))
    return ChatChannelPermission.objects.filter(
        channel=channel,
        user__isnull=False
    ).exclude(
        user__user_id__in=committee_member_ids
    ).select_related('user').defer(*member_defer('user')).order_by('user__member_status', 'user__name')


def chat_guest_candidates(committee):
    """Who the page offers as guests: active + alumni, excluding committee members."""
    committee_member_ids = list(committee.members.values_list('user_id', flat=True))
    return ParliamentUser.objects.filter(
        member_status__in=['Active', 'Alumni']
    ).exclude(
        user_id__in=committee_member_ids
    ).order_by('member_status', 'name')


def parse_guest_expiry(raw):
    """
    ``(expires_at, error)`` for the optional expiry: empty is none; otherwise an
    ISO date-time in the future (one without an offset is local time).
    """
    raw = (raw or '').strip()
    if not raw:
        return None, None
    try:
        from datetime import datetime
        expires_at = datetime.fromisoformat(raw)
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
    except (ValueError, TypeError):
        return None, 'Invalid expiry date format'
    if expires_at <= timezone.now():
        return None, 'Expiry date must be in the future'
    return expires_at, None


def save_guest_permission(channel, guest_user, flags, expires_at):
    """Grant or replace one guest's permission. Returns ``(perm, created)``."""
    return ChatChannelPermission.objects.update_or_create(
        channel=channel,
        user=guest_user,
        defaults={**{f: bool(flags.get(f)) for f in GUEST_FLAGS}, 'expires_at': expires_at},
    )


def bulk_save_guest_permissions(committee, channel, user_ids, flags, expires_at):
    """
    The same grant for several people; committee members and unknown ids are
    skipped. Returns ``(added names, skipped ids)``.
    """
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
        save_guest_permission(channel, guest_user, flags, expires_at)
        added.append(guest_user.name)
    return added, skipped


def update_guest_permission_row(permission, flags, expires_at):
    """Overwrite an existing guest permission's four switches and expiry."""
    for f in GUEST_FLAGS:
        setattr(permission, f, bool(flags.get(f)))
    permission.expires_at = expires_at
    permission.save()
    return permission


def remove_guest_permissions(channel, user_ids):
    """Delete these people's permissions on the channel. Returns how many went."""
    deleted_count, _ = ChatChannelPermission.objects.filter(
        channel=channel,
        user__user_id__in=user_ids
    ).delete()
    return deleted_count


def _post_flags(request):
    return {f: request.POST.get(f) == 'true' for f in GUEST_FLAGS}


# ── Pages ───────────────────────────────────────────────────────────────────

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

    guest_permissions = chat_guest_permissions(committee, channel)
    available_users = chat_guest_candidates(committee)

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
    flags = _post_flags(request)

    if not user_id:
        return JsonResponse({'error': 'User ID is required'}, status=400)

    if not flags['can_read'] and not flags['can_write']:
        return JsonResponse({'error': 'Guest must have at least read or write permission'}, status=400)

    expires_at, error = parse_guest_expiry(request.POST.get('expires_at', ''))
    if error:
        return JsonResponse({'error': error}, status=400)

    try:
        guest_user = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    if committee.is_member(guest_user):
        return JsonResponse({'error': 'This user is already a committee member'}, status=400)

    perm, created = save_guest_permission(channel, guest_user, flags, expires_at)

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
    flags = _post_flags(request)

    if not user_ids:
        return JsonResponse({'error': 'No users selected'}, status=400)

    if not flags['can_read'] and not flags['can_write']:
        return JsonResponse({'error': 'Must grant at least read or write permission'}, status=400)

    expires_at, error = parse_guest_expiry(request.POST.get('expires_at', ''))
    if error:
        return JsonResponse({'error': error}, status=400)

    added, skipped = bulk_save_guest_permissions(committee, channel, user_ids, flags, expires_at)

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

    flags = _post_flags(request)

    if not flags['can_read'] and not flags['can_write']:
        return JsonResponse({'error': 'Guest must retain at least read or write permission'}, status=400)

    # Optional expiry date (empty string clears it)
    expires_at, error = parse_guest_expiry(request.POST.get('expires_at', ''))
    if error:
        return JsonResponse({'error': error}, status=400)

    update_guest_permission_row(permission, flags, expires_at)

    return JsonResponse({
        'success': True,
        'message': f'Updated permissions for {guest_user.name}',
        **flags,
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

    if remove_guest_permissions(channel, [guest_user.user_id]) > 0:
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

    deleted_count = remove_guest_permissions(channel, user_ids)

    return JsonResponse({
        'success': True,
        'message': f'Removed {deleted_count} guest(s)',
        'removed_ids': user_ids,
    })
