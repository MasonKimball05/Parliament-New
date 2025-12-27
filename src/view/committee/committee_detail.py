from src.models import *
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages

@login_required
def committee_detail(request, code):
    """Display details for a specific committee"""
    committee = get_object_or_404(Committee.objects.select_related('role'), code=code)

    # Check if user has access to this committee
    user = request.user
    is_member = committee.members.filter(pk=user.pk).exists()
    is_chair = committee.chairs.filter(pk=user.pk).exists()
    is_advisor = committee.advisors.filter(pk=user.pk).exists()
    is_voting_member = committee.voting_members.filter(pk=user.pk).exists()
    is_vp = committee.is_vp(user)

    if not (is_member or is_chair or is_advisor or is_vp):
        messages.error(request, 'You do not have access to this committee.')
        return redirect('committee_index')

    # Get committee VP
    committee_vp = committee.get_vp()

    # Get or create committee permissions
    try:
        permissions = CommitteePermissions.objects.get(committee=committee, user=user)
    except CommitteePermissions.DoesNotExist:
        permissions = CommitteePermissions.objects.create(
            committee=committee,
            user=user,
            can_view_docs=True,
            can_vote=is_voting_member,
            can_upload_docs=is_chair,
            can_manage_members=False,
            can_view_results=True
        )

    # Get all active users for different roles
    all_users = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # For voting members, only show current members and chairs
    eligible_voters = (committee.members.all() | committee.chairs.all()).distinct().order_by('name')

    context = {
        'committee': committee,
        'committee_vp': committee_vp,
        'is_chair': is_chair,
        'is_advisor': is_advisor,
        'is_member': is_member,
        'is_voting_member': is_voting_member,
        'is_vp': is_vp,
        'can_manage': is_vp or user.is_admin,
        'permissions': permissions,
        'all_users': all_users,
        'eligible_voters': eligible_voters,
    }

    # If this is the Kai committee and user is a chair, add Kai reports
    if committee.code == 'KAI' and (is_chair or user.is_admin):
        try:
            from src.models import KaiReport
            # Try select_related for production
            try:
                kai_reports = list(KaiReport.objects.filter(
                    status__in=['pending', 'reviewed']
                ).select_related('submitted_by', 'reviewed_by', 'targeted_to').order_by('-submitted_at')[:10])
            except:
                # Fallback for test database without select_related
                kai_reports = list(KaiReport.objects.filter(
                    status__in=['pending', 'reviewed']
                ).order_by('-submitted_at')[:10])

            kai_report_count = KaiReport.objects.filter(status='pending').count()
            context['kai_reports'] = kai_reports
            context['kai_report_count'] = kai_report_count
        except Exception:
            # KaiReport table may not exist yet if migrations haven't been run
            # Don't add kai_reports to context at all
            pass

    return render(request, 'committee/detail.html', context)
