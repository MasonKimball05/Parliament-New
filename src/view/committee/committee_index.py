import json

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from src.models import Committee, Role, ParliamentUser, CommitteeLegislation, CommitteeDocument, CommitteeMinutes, ActivityLog, log_admin_action
from src.forms import CommitteeCreateForm
from src.constants import MemberStatus
from src.feature_flag_decorators import require_page_enabled
from src.decorators import admin_required, officer_required
import logging

logger = logging.getLogger('function_calls')

@login_required
@require_page_enabled('committee_index')
def committee_index(request):
    """Display all committees the user is associated with"""
    user = request.user
    show_all = request.GET.get('show_all') == 'true' and user.is_admin

    # Get all committees where user is a member, chair, or advisor with select_related for role
    member_committees = user.committees.select_related('role').all()
    chair_committees = user.chair_roles.select_related('role').all()
    advisor_committees = user.advisor_roles.select_related('role').all()
    voting_committees = user.committee_voters.select_related('role').all()

    # Combine and remove duplicates
    user_committees = (member_committees | chair_committees | advisor_committees).distinct()

    show_archived = request.GET.get('show_archived') == 'true' and (user.is_admin or user.member_type == 'Officer')

    # Get all committees for dropdown and admin view
    all_committees_query = Committee.objects.select_related('role').all().order_by('name')

    # Filter by visibility (unless show_all for admin)
    if show_all:
        all_committees_list = list(all_committees_query)
    else:
        all_committees_list = [c for c in all_committees_query if c.is_visible_to(user)]

    # Exclude archived unless explicitly requested
    if not show_archived:
        all_committees_list = [c for c in all_committees_list if not c.is_archived]

    # v3.17.3: this view was ~9 queries per committee — dev mode reported
    # `get_vp` firing 14× twice over (once for its redundant `.exists()`, once
    # for the `.first()`), and that was only the part it grouped. Per committee
    # it also ran four `.exists()` calls for the viewer's own roles and three
    # `.count()` calls for the badges. At 14 committees that is ~150 queries for
    # one page. Everything below is now batched: the per-committee work is
    # dictionary lookups, and the query count no longer depends on how many
    # committees the chapter has.

    # VPs for every committee on the page — two queries, not two per committee.
    vp_by_committee = Committee.vp_map(all_committees_list)

    # Prepare all committees info for dropdown (filtered by visibility)
    all_committees_info = [
        {'committee': committee, 'vp': vp_by_committee.get(committee.pk)}
        for committee in all_committees_list
    ]

    # Count archived committees the user is a member of (for toggle pill)
    archived_count = sum(1 for c in user_committees if c.is_archived and c.is_visible_to(user))

    # Determine which committees to display in main section
    if show_all:
        display_committees = all_committees_list
    else:
        display_committees = [c for c in user_committees if c.is_visible_to(user)]
        if not show_archived:
            display_committees = [c for c in display_committees if not c.is_archived]

    # The viewer's own role in each committee: four id sets, fetched once,
    # instead of four `.exists()` queries per committee.
    chair_ids = set(chair_committees.values_list('id', flat=True))
    advisor_ids = set(advisor_committees.values_list('id', flat=True))
    member_ids = set(member_committees.values_list('id', flat=True))
    voting_ids = set(voting_committees.values_list('id', flat=True))

    # Badge counts: one aggregate over the committees actually being shown,
    # replacing three `.count()` calls per committee. `distinct=True` is
    # load-bearing — three multi-valued joins in one annotate() multiply each
    # other's rows and inflate all three counts without it.
    display_ids = [c.pk for c in display_committees]
    counts_by_committee = {
        row['id']: row
        for row in Committee.objects.filter(id__in=display_ids).values('id').annotate(
            member_total=Count('members', distinct=True),
            chair_total=Count('chairs', distinct=True),
            advisor_total=Count('advisors', distinct=True),
        )
    }

    # `display_committees` can come from `user_committees` rather than
    # `all_committees_list`, so it may hold committees the VP map above never
    # saw. One more batched call covers them; it is a no-op when it doesn't.
    missing_vp = [c for c in display_committees if c.pk not in vp_by_committee]
    if missing_vp:
        vp_by_committee.update(Committee.vp_map(missing_vp))

    committees_with_roles = []
    for committee in display_committees:
        roles = []
        if committee.id in chair_ids:
            roles.append('Chair')
        if committee.id in advisor_ids:
            roles.append('Advisor')
        if committee.id in member_ids:
            roles.append('Member')

        counts = counts_by_committee.get(committee.pk, {})
        committees_with_roles.append({
            'committee': committee,
            'roles': roles,
            'is_voting_member': committee.id in voting_ids,
            'committee_vp': vp_by_committee.get(committee.pk),
            'member_count': counts.get('member_total', 0),
            'chair_count': counts.get('chair_total', 0),
            'advisor_count': counts.get('advisor_total', 0),
        })

    context = {
        'committees': committees_with_roles,
        'all_committees_info': all_committees_info,
        'show_all': show_all,
        'show_archived': show_archived,
        'archived_count': archived_count,
        'is_test_server': settings.DEBUG,  # Test server runs with DEBUG=True
        'can_see_archived_toggle': user.is_admin or user.member_type == 'Officer',
    }

    return render(request, 'committee/committee_index.html', context)


@login_required
@admin_required
def create_committee(request):
    """Create a new committee (admin only)"""
    if request.method == 'POST':
        form = CommitteeCreateForm(request.POST)
        if form.is_valid():
            committee = form.save()

            logger.info(f"{request.user.username} created committee: {committee.name} ({committee.code})")
            messages.success(request, f'Committee "{committee.name}" created successfully.')
            return redirect('committee_home', code=committee.code)
    else:
        form = CommitteeCreateForm()

    # Get roles for the dropdown
    roles = Role.objects.all().order_by('name')

    # Get active members for the multi-select
    members = ParliamentUser.objects.filter(member_status=MemberStatus.ACTIVE).order_by('name')

    context = {
        'form': form,
        'roles': roles,
        'members': members,
    }

    return render(request, 'committee/create_committee.html', context)


@login_required
@officer_required
def manage_committees(request):
    """Manage all committees - list, add, edit, delete (officers and admins)"""
    # v3.17.4: three COUNTs per committee became one conditional aggregate —
    # the same fix `committee_index` got in v3.17.3, which was never carried to
    # this page. `distinct=True` is load-bearing: three multi-valued joins in one
    # annotate() multiply each other's rows and inflate all three counts.
    committees = list(
        Committee.objects.select_related('role')
        .annotate(
            member_total=Count('members', distinct=True),
            chair_total=Count('chairs', distinct=True),
            advisor_total=Count('advisors', distinct=True),
        )
        .order_by('name')
    )

    committees_data = [
        {
            'committee': committee,
            'member_count': committee.member_total,
            'chair_count': committee.chair_total,
            'advisor_count': committee.advisor_total,
        }
        for committee in committees
    ]

    # Get roles for the create form
    roles = Role.objects.all().order_by('name')

    # Get active members for the multi-select
    members = ParliamentUser.objects.filter(member_status=MemberStatus.ACTIVE).order_by('name')

    context = {
        'committees_data': committees_data,
        'total_committees': len(committees),
        'roles': roles,
        'members': members,
        'form': CommitteeCreateForm(),
    }

    return render(request, 'committee/manage_committees.html', context)


@login_required
@officer_required
@require_http_methods(['GET', 'POST'])
def committee_detail_api(request, committee_id):
    """Get or update a committee's details."""
    committee = get_object_or_404(Committee, id=committee_id)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'committee': {
                'id': committee.id,
                'name': committee.name,
                'code': committee.code,
                'role_id': committee.role_id,
                'role_name': committee.role.name if committee.role else None,
                'is_ad_hoc': committee.is_ad_hoc,
                'ad_hoc_expiration': committee.ad_hoc_expiration.isoformat() if committee.ad_hoc_expiration else None,
                'is_active': committee.is_active,
                'is_archived': committee.is_archived,
                'member_ids': list(committee.members.values_list('user_id', flat=True)),
                'chair_ids': list(committee.chairs.values_list('user_id', flat=True)),
            }
        })

    # POST - Update committee
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    # Track changes
    changes = []

    if 'name' in data and data['name'] != committee.name:
        changes.append(f"name: {committee.name} -> {data['name']}")
        committee.name = data['name']

    if 'code' in data:
        new_code = data['code'].upper().strip()
        if new_code != committee.code:
            # Check for duplicate code
            if Committee.objects.filter(code__iexact=new_code).exclude(id=committee.id).exists():
                return JsonResponse({'success': False, 'error': 'A committee with this code already exists.'}, status=400)
            changes.append(f"code: {committee.code} -> {new_code}")
            committee.code = new_code

    if 'role_id' in data:
        new_role_id = data['role_id'] if data['role_id'] else None
        if new_role_id != committee.role_id:
            old_role_name = committee.role.name if committee.role else 'None'
            if new_role_id:
                new_role = Role.objects.filter(id=new_role_id).first()
                new_role_name = new_role.name if new_role else 'None'
                committee.role = new_role
            else:
                new_role_name = 'None'
                committee.role = None
            changes.append(f"role: {old_role_name} -> {new_role_name}")

    if 'is_ad_hoc' in data:
        new_val = bool(data['is_ad_hoc'])
        if new_val != committee.is_ad_hoc:
            changes.append(f"is_ad_hoc: {committee.is_ad_hoc} -> {new_val}")
            committee.is_ad_hoc = new_val
        # Clear expiration date if no longer ad-hoc
        if not new_val and committee.ad_hoc_expiration:
            changes.append(f"ad_hoc_expiration cleared")
            committee.ad_hoc_expiration = None

    if 'ad_hoc_expiration' in data:
        from datetime import datetime
        new_expiration = data['ad_hoc_expiration']
        if new_expiration:
            try:
                new_date = datetime.strptime(new_expiration, '%Y-%m-%d').date()
            except ValueError:
                new_date = None
        else:
            new_date = None

        if new_date != committee.ad_hoc_expiration:
            old_val = committee.ad_hoc_expiration.isoformat() if committee.ad_hoc_expiration else 'None'
            new_val = new_date.isoformat() if new_date else 'None'
            changes.append(f"ad_hoc_expiration: {old_val} -> {new_val}")
            committee.ad_hoc_expiration = new_date

    if 'is_active' in data:
        new_val = bool(data['is_active'])
        if new_val != committee.is_active:
            changes.append(f"is_active: {committee.is_active} -> {new_val}")
            committee.is_active = new_val

    if 'is_archived' in data:
        new_val = bool(data['is_archived'])
        if new_val != committee.is_archived:
            changes.append(f"is_archived: {committee.is_archived} -> {new_val}")
            committee.is_archived = new_val

    committee.save()

    # Update members if provided
    if 'member_ids' in data:
        new_members = ParliamentUser.objects.filter(user_id__in=data['member_ids'])
        committee.members.set(new_members)
        changes.append(f"members updated ({len(data['member_ids'])} members)")

    if 'chair_ids' in data:
        new_chairs = ParliamentUser.objects.filter(user_id__in=data['chair_ids'])
        committee.chairs.set(new_chairs)
        changes.append(f"chairs updated ({len(data['chair_ids'])} chairs)")

    # Log the activity
    if changes:
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.get_display_name()} updated committee {committee.name}: {", ".join(changes)}',
            request=request,
            metadata={
                'action': 'edit_committee',
                'committee_id': committee.id,
                'committee_name': committee.name,
                'changes': changes,
            }
        )
        logger.info(f"User {request.user.user_id} updated committee {committee.id}: {changes}")
        membership_changes = [c for c in changes if any(k in c for k in ('role', 'chair', 'member'))]
        if membership_changes:
            log_admin_action(
                actor=request.user, action='role_changed', request=request,
                target_repr=committee.name,
                detail=', '.join(membership_changes),
            )

    return JsonResponse({
        'success': True,
        'message': f'Committee "{committee.name}" updated successfully.',
        'changes': changes,
    })


@login_required
@officer_required
@require_POST
def delete_committee(request, committee_id):
    """Delete a committee."""
    committee = get_object_or_404(Committee, id=committee_id)

    committee_name = committee.name
    committee_code = committee.code
    committee_id_val = committee.id

    # Check if committee has any important data
    has_legislation = CommitteeLegislation.objects.filter(committee=committee).exists()
    has_documents = CommitteeDocument.objects.filter(committee=committee).exists()
    has_minutes = CommitteeMinutes.objects.filter(committee=committee).exists()

    if has_legislation or has_documents or has_minutes:
        return JsonResponse({
            'success': False,
            'error': f'Cannot delete committee "{committee_name}" - it has associated legislation, documents, or minutes. Please archive these first or set the committee to inactive instead.'
        }, status=400)

    committee.delete()

    # Log the activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} deleted committee {committee_name} ({committee_code})',
        request=request,
        metadata={
            'action': 'delete_committee',
            'committee_id': committee_id_val,
            'committee_name': committee_name,
            'committee_code': committee_code,
        }
    )
    logger.info(f"User {request.user.user_id} deleted committee {committee_id_val}: {committee_name}")

    return JsonResponse({
        'success': True,
        'message': f'Committee "{committee_name}" deleted successfully.',
    })
