from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from src.models import (
    Committee, CommitteePermissions, CommitteeDocument,
    CommitteeVote, ParliamentUser
)
from src.constants import MemberType, MemberStatus
from datetime import timedelta
from src.feature_flag_decorators import require_page_enabled

@login_required
@require_page_enabled('committee_home')
def committee_home(request, code):
    committee = get_object_or_404(Committee.objects.select_related('role'), code=code)

    user = request.user

    # Check user roles
    is_member = committee.members.filter(pk=user.pk).exists()
    is_chair = committee.chairs.filter(pk=user.pk).exists()
    is_advisor = committee.advisors.filter(pk=user.pk).exists()
    is_voting_member = committee.voting_members.filter(pk=user.pk).exists()
    is_vp = committee.role and user.roles.filter(id=committee.role.id).exists()
    is_committee_admin = committee.admin == user if committee.admin else False

    # Access check
    has_access = (is_member or is_chair or is_advisor or is_vp or
                  user.is_admin or is_committee_admin)

    # Special test server access for Slating Committee
    is_test_slating_admin = False
    if committee.is_slating_committee and settings.DEBUG and user.user_id == '73':
        has_access = True
        is_test_slating_admin = True

    if not has_access:
        messages.error(request, 'You do not have access to this committee.')
        return redirect('committee_index')

    can_manage = is_chair or is_vp or user.is_admin or is_committee_admin or is_test_slating_admin

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

    # Eligible user lists for member management modal
    eligible_members = ParliamentUser.objects.filter(
        member_status=MemberStatus.ACTIVE
    ).order_by('name')

    eligible_chairs = committee.members.exclude(
        pk__in=committee.chairs.all()
    ).order_by('name')

    eligible_advisors = ParliamentUser.objects.filter(
        Q(member_status=MemberStatus.ACTIVE) | Q(member_type=MemberType.ADVISOR)
    ).order_by('name')

    eligible_voters = (committee.members.all() | committee.chairs.all()).exclude(
        pk__in=committee.voting_members.all()
    ).distinct().order_by('name')

    # Stats
    total_members = committee.members.count()
    total_chairs = committee.chairs.count()
    total_advisors = committee.advisors.count()
    all_potential_voters = (committee.members.all() | committee.chairs.all()).distinct()
    voting_members_count = all_potential_voters.exclude(pk__in=committee.voting_members.all()).count()
    total_people = (committee.members.all() | committee.chairs.all() | committee.advisors.all()).distinct().count()

    # Document stats
    total_documents = CommitteeDocument.objects.filter(committee=committee).count()
    recent_documents = CommitteeDocument.objects.filter(
        committee=committee
    ).select_related('uploaded_by').order_by('-uploaded_at')[:5]
    published_documents = CommitteeDocument.objects.filter(
        committee=committee, published_to_chapter=True
    ).count()

    # Vote stats
    thirty_days_ago = timezone.now() - timedelta(days=30)
    active_votes = CommitteeVote.objects.filter(
        legislation__committee=committee, is_active=True
    ).count()
    recent_votes = CommitteeVote.objects.filter(
        legislation__committee=committee,
        created_at__gte=thirty_days_ago
    ).order_by('-created_at')[:5]
    total_votes = CommitteeVote.objects.filter(
        legislation__committee=committee
    ).count()

    context = {
        'committee': committee,
        'committee_vp': committee_vp,
        'permissions': permissions,
        'is_member': is_member,
        'is_chair': is_chair,
        'is_advisor': is_advisor,
        'is_voting_member': is_voting_member,
        'is_vp': is_vp,
        'is_committee_admin': is_committee_admin,
        'can_manage': can_manage,
        # Eligible lists for modal
        'eligible_members': eligible_members,
        'eligible_chairs': eligible_chairs,
        'eligible_advisors': eligible_advisors,
        'eligible_voters': eligible_voters,
        # Stats
        'stats': {
            'total_members': total_members,
            'total_chairs': total_chairs,
            'total_advisors': total_advisors,
            'voting_members_count': voting_members_count,
            'total_people': total_people,
            'total_documents': total_documents,
            'published_documents': published_documents,
            'active_votes': active_votes,
            'total_votes': total_votes,
        },
        'recent_documents': recent_documents,
        'recent_votes': recent_votes,
    }

    # Kai committee: add reports for chairs/admins
    if committee.is_kai_committee and (is_chair or user.is_admin):
        try:
            from src.models import KaiReport
            try:
                kai_reports = list(KaiReport.objects.filter(
                    status__in=['pending', 'reviewed']
                ).select_related('submitted_by', 'reviewed_by', 'targeted_to').order_by('-submitted_at')[:10])
            except Exception:
                kai_reports = list(KaiReport.objects.filter(
                    status__in=['pending', 'reviewed']
                ).order_by('-submitted_at')[:10])
            context['kai_reports'] = kai_reports
            context['kai_report_count'] = KaiReport.objects.filter(status='pending').count()
        except Exception:
            pass

    # Slating committee: add election periods
    if committee.is_slating_committee:
        try:
            from src.models import SlatingPeriod
            context['slating_periods'] = SlatingPeriod.objects.all().order_by('-created_at')[:10]
        except Exception:
            pass

    return render(request, 'committee/committee_home.html', context)
