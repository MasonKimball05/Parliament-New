"""
Education committee member permissions — v3.32.0.

Mason: "can we also add an education permissions dashboard so the admin of
the committee can set what permissions the members have whether it's none,
some permissions; view and grade tasks (2 separate perms), or all
permissions where they can make tasks and whatnot. Kai has a version of
this already."

Mirrors `src/view/committee/manage_kai_permissions.py` almost line for
line, at his explicit direction. One deliberate departure from that file:
Kai uses `committee.is_chair(request.user)` (the exec-board-inclusive
method) to decide who may MANAGE permissions, while `_is_kai_chair`
(real chairs only) decides who HAS full access when using Kai day to day —
two different definitions of "chair" for two different questions in the
same feature. That split was never a deliberate design decision on Kai's
part (see CLAUDE.md's research on it); this file uses ONE definition,
real committee chairs only, for both questions, to avoid reintroducing it.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from src.models import Committee, ParliamentUser, EducationMemberPermission
from src.models.users import member_defer

#: All permission fields exposed in the UI, in display order.
EDUCATION_PERM_FIELDS = [
    'can_view_submissions',
    'can_grade_submissions',
    'can_manage_tasks',
    'can_adjust_time',
]


def may_manage_education_permissions(user, committee):
    """
    Return True if `user` may manage education permissions for `committee`.
    Real chair (not `Committee.is_chair()`, which also returns True for any
    member of an `is_exec_board`-flagged committee) or admin — the same
    definition `_get_education_access` uses for full access, on purpose; see
    the module docstring. Shared with the app.
    """
    return committee.chairs.filter(pk=user.pk).exists() or user.is_admin


def _require_education_chair(request, committee):
    return may_manage_education_permissions(request.user, committee)


def education_permission_rows(committee):
    """
    Every chair, member and voting member of `committee`, by name, with what
    each may do: `{member, is_chair, <field>: bool...}`. Chairs show full
    access (their switches are disabled). Shared with the app.
    """
    chairs_pks = set(committee.chairs.values_list('pk', flat=True))
    members_pks = set(committee.members.values_list('pk', flat=True))
    voting_pks = set(committee.voting_members.values_list('pk', flat=True))
    all_pks = chairs_pks | members_pks | voting_pks

    committee_members = ParliamentUser.objects.filter(pk__in=all_pks).order_by('name')

    # Existing permissions keyed by user pk for fast lookup
    existing_perms = {
        p.user_id: p
        for p in EducationMemberPermission.objects.filter(committee=committee)
        .select_related('user').defer(*member_defer('user'))
    }

    # Build flat rows; chairs are flagged as full-access (checkboxes disabled in template)
    member_rows = []
    for member in committee_members:
        is_chair = member.pk in chairs_pks
        perm = existing_perms.get(member.pk)
        row = {'member': member, 'is_chair': is_chair}
        for field in EDUCATION_PERM_FIELDS:
            row[field] = True if is_chair else (getattr(perm, field) if perm else False)
        member_rows.append(row)
    return member_rows


def set_education_permission(committee, member, flags, granted_by):
    """
    Save `member`'s education permissions (`flags`: field -> bool; a field
    left out is False, as an unticked box). Returns (perm, None), or
    (None, error message) for a chair, who already has everything. Shared
    with the app.
    """
    # Chairs already have full access — don't create redundant permission rows
    if committee.chairs.filter(pk=member.pk).exists():
        return None, 'Chairs always have full access; no explicit permission needed'
    defaults = {field: bool(flags.get(field)) for field in EDUCATION_PERM_FIELDS}
    defaults['granted_by'] = granted_by
    perm, _ = EducationMemberPermission.objects.update_or_create(
        committee=committee,
        user=member,
        defaults=defaults,
    )
    return perm, None


def reset_education_permission_rows(committee):
    """Delete every permission row for `committee`; returns how many. Shared with the app."""
    deleted_count, _ = EducationMemberPermission.objects.filter(committee=committee).delete()
    return deleted_count


def _serialize_education_perm(perm):
    data = {
        'user_id': perm.user.user_id,
        'user_name': perm.user.name,
        'member_type': perm.user.member_type,
    }
    for field in EDUCATION_PERM_FIELDS:
        data[field] = getattr(perm, field)
    return data


@login_required
def manage_education_permissions(request, code):
    """Education chair/admin: manage per-member education access."""
    committee = get_object_or_404(Committee, code=code, is_education_committee=True)

    if not _require_education_chair(request, committee):
        messages.error(request, 'Only education chairs can manage member permissions.')
        return redirect('committee_home', code=code)

    context = {
        'committee': committee,
        'member_rows': education_permission_rows(committee),
        'perm_fields': EDUCATION_PERM_FIELDS,
    }
    return render(request, 'committee/manage_education_permissions.html', context)


@login_required
@require_http_methods(['POST'])
def update_education_member_permission(request, code, user_id):
    """Set education permissions for a committee member (AJAX)."""
    committee = get_object_or_404(Committee, code=code, is_education_committee=True)

    if not _require_education_chair(request, committee):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        member = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    perm, error = set_education_permission(
        committee, member, {field: request.POST.get(field) == 'true' for field in EDUCATION_PERM_FIELDS},
        request.user)
    if error:
        return JsonResponse({'error': error}, status=400)

    return JsonResponse({'success': True, **_serialize_education_perm(perm)})


@login_required
@require_http_methods(['POST'])
def reset_education_permissions(request, code):
    """Wipe all EducationMemberPermission rows for this committee."""
    committee = get_object_or_404(Committee, code=code, is_education_committee=True)

    if not _require_education_chair(request, committee):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    deleted_count = reset_education_permission_rows(committee)
    return JsonResponse({
        'success': True,
        'message': f'Reset {deleted_count} member permission(s) to default (no access).',
        'deleted': deleted_count,
    })
