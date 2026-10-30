"""
Chairs designate who on a committee can take minutes, manage documents and
manage members (10-29-26). The flags already lived on `CommitteePermissions`;
only `can_take_minutes` was ever checked, and nothing let a chair set any of
them. This page sets them, and `can_manage_documents` / `can_manage_members`
are what the document and member views now check.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from src.models import Committee, CommitteePermissions

#: (field, label) — the designations a chair can hand out.
DESIGNATIONS = (
    ('can_take_minutes', 'Take minutes'),
    ('can_upload_docs', 'Manage documents'),
    ('can_manage_members', 'Manage members'),
)


def can_designate(user, committee):
    """Chairs (including exec board), the committee's VP and site admins."""
    return user.is_admin or committee.is_chair(user) or committee.is_vp(user)


def _flag(user, committee, field):
    return CommitteePermissions.objects.filter(committee=committee, user=user, **{field: True}).exists()


def can_manage_documents(user, committee):
    """Upload, replace, publish and delete this committee's documents."""
    return can_designate(user, committee) or _flag(user, committee, 'can_upload_docs')


def can_manage_members(user, committee):
    return can_designate(user, committee) or _flag(user, committee, 'can_manage_members')


@login_required
def committee_member_permissions(request, code):
    committee = get_object_or_404(Committee, code=code)
    if not can_designate(request.user, committee):
        return HttpResponseForbidden('Only the committee’s chairs can change member permissions.')

    chair_ids = set(committee.chairs.values_list('pk', flat=True))
    people = [m for m in (committee.members.all() | committee.advisors.all()).distinct().order_by('name')
              if m.pk not in chair_ids]

    if request.method == 'POST':
        for member in people:
            values = {f: request.POST.get(f'{f}_{member.pk}') == 'on' for f, _ in DESIGNATIONS}
            CommitteePermissions.objects.update_or_create(
                committee=committee, user=member,
                defaults=values,
                create_defaults={**values, 'can_view_docs': True,
                                 'can_vote': committee.voting_members.filter(pk=member.pk).exists()},
            )
        messages.success(request, 'Member permissions saved.')
        return redirect('committee_member_permissions', code=code)

    current = {p.user_id: p for p in CommitteePermissions.objects.filter(committee=committee, user__in=people)}
    rows = [{
        'member': m,
        'flags': [(f, label, bool(current.get(m.pk) and getattr(current[m.pk], f))) for f, label in DESIGNATIONS],
    } for m in people]
    return render(request, 'committee/member_permissions.html', {
        'committee': committee,
        'rows': rows,
        'designations': DESIGNATIONS,
    })
