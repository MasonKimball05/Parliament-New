from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from src.models import Committee, ParliamentUser, KaiMemberPermission

# All permission fields exposed in the UI
KAI_PERM_FIELDS = [
    'can_view_report_list',
    'can_view_report_details',
    'can_view_submitter_identity',
    'can_view_accused_identity',
    'can_edit_open_cases',
    'can_add_activity',
    'can_close_cases',
]


def _require_kai_chair(request, committee):
    """Return True if user may manage Kai permissions, otherwise redirect."""
    return committee.is_chair(request.user) or request.user.is_admin


def _serialize_kai_perm(perm):
    data = {
        'user_id': perm.user.user_id,
        'user_name': perm.user.name,
        'member_type': perm.user.member_type,
    }
    for field in KAI_PERM_FIELDS:
        data[field] = getattr(perm, field)
    return data


@login_required
def manage_kai_permissions(request, code):
    """Kai chair/admin: manage per-member Kai access for any chapter member."""
    committee = get_object_or_404(Committee, code=code, is_kai_committee=True)

    if not _require_kai_chair(request, committee):
        messages.error(request, 'Only Kai chairs can manage member permissions.')
        return redirect('committee_home', code=code)

    chairs_pks = set(committee.chairs.values_list('pk', flat=True))
    members_pks = set(committee.members.values_list('pk', flat=True))
    voting_pks = set(committee.voting_members.values_list('pk', flat=True))
    all_pks = chairs_pks | members_pks | voting_pks

    committee_members = ParliamentUser.objects.filter(pk__in=all_pks).order_by('name')

    # Existing permissions keyed by user pk for fast lookup
    existing_perms = {
        p.user_id: p
        for p in KaiMemberPermission.objects.filter(committee=committee).select_related('user')
    }

    # Build flat rows; chairs are flagged as full-access (checkboxes disabled in template)
    member_rows = []
    for member in committee_members:
        is_chair = member.pk in chairs_pks
        perm = existing_perms.get(member.pk)
        row = {'member': member, 'is_chair': is_chair}
        for field in KAI_PERM_FIELDS:
            row[field] = True if is_chair else (getattr(perm, field) if perm else False)
        member_rows.append(row)

    context = {
        'committee': committee,
        'member_rows': member_rows,
        'perm_fields': KAI_PERM_FIELDS,
    }
    return render(request, 'committee/manage_kai_permissions.html', context)


@login_required
@require_http_methods(['POST'])
def update_kai_member_permission(request, code, user_id):
    """Set Kai permissions for any chapter member (AJAX)."""
    committee = get_object_or_404(Committee, code=code, is_kai_committee=True)

    if not _require_kai_chair(request, committee):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        member = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    # Chairs already have full access — don't create redundant permission rows
    if committee.is_chair(member):
        return JsonResponse({'error': 'Chairs always have full access; no explicit permission needed'}, status=400)

    defaults = {field: request.POST.get(field) == 'true' for field in KAI_PERM_FIELDS}
    defaults['granted_by'] = request.user

    perm, _ = KaiMemberPermission.objects.update_or_create(
        committee=committee,
        user=member,
        defaults=defaults,
    )

    return JsonResponse({'success': True, **_serialize_kai_perm(perm)})


@login_required
@require_http_methods(['POST'])
def reset_kai_permissions(request, code):
    """Wipe all KaiMemberPermission rows for this committee."""
    committee = get_object_or_404(Committee, code=code, is_kai_committee=True)

    if not _require_kai_chair(request, committee):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    deleted_count, _ = KaiMemberPermission.objects.filter(committee=committee).delete()
    return JsonResponse({
        'success': True,
        'message': f'Reset {deleted_count} member permission(s) to default (no access).',
        'deleted': deleted_count,
    })
