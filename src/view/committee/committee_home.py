from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.db.models import Count, Q
from src.models import (
    Committee, CommitteePermissions, CommitteeDocument,
    CommitteeVote
)
from datetime import timedelta
from src.feature_flag_decorators import require_page_enabled

@require_page_enabled('committee_home')
def committee_home(request, code):
    committee = get_object_or_404(Committee.objects.select_related('role'), code=code)
    perm = CommitteePermissions.objects.filter(
        committee=committee, user=request.user
    ).first()

    # Get committee VP
    committee_vp = committee.get_vp()

    # Check user roles
    user = request.user
    is_chair = committee.chairs.filter(user_id=user.user_id).exists()
    is_advisor = committee.advisors.filter(user_id=user.user_id).exists()
    is_vp = committee.role and user.roles.filter(id=committee.role.id).exists()
    can_manage = is_chair or is_vp or user.is_admin

    # Get committee statistics
    total_members = committee.members.count()
    total_chairs = committee.chairs.count()
    total_advisors = committee.advisors.count()

    # Get voting members count - those who CAN vote (members + chairs who are NOT in voting_members exclusion list)
    all_potential_voters = (committee.members.all() | committee.chairs.all()).distinct()
    voting_members_count = all_potential_voters.exclude(pk__in=committee.voting_members.all()).count()

    # Get total people in committee (members + chairs + advisors)
    total_people = (committee.members.all() | committee.chairs.all() | committee.advisors.all()).distinct().count()

    # Get document stats
    total_documents = CommitteeDocument.objects.filter(committee=committee).count()
    recent_documents = CommitteeDocument.objects.filter(
        committee=committee
    ).select_related('uploaded_by').order_by('-uploaded_at')[:5]

    # Get published documents count
    published_documents = CommitteeDocument.objects.filter(
        committee=committee,
        published_to_chapter=True
    ).count()

    # Get recent votes (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    active_votes = CommitteeVote.objects.filter(
        legislation__committee=committee,
        is_active=True
    ).count()
    recent_votes = CommitteeVote.objects.filter(
        legislation__committee=committee,
        created_at__gte=thirty_days_ago
    ).order_by('-created_at')[:5]

    # Get total votes count
    total_votes = CommitteeVote.objects.filter(
        legislation__committee=committee
    ).count()

    context = {
        "committee": committee,
        "perm": perm,
        "committee_vp": committee_vp,
        "is_chair": is_chair,
        "is_advisor": is_advisor,
        "is_vp": is_vp,
        "can_manage": can_manage,
        # Stats
        "stats": {
            "total_members": total_members,
            "total_chairs": total_chairs,
            "total_advisors": total_advisors,
            "voting_members_count": voting_members_count,
            "total_people": total_people,
            "total_documents": total_documents,
            "published_documents": published_documents,
            "active_votes": active_votes,
            "total_votes": total_votes,
        },
        # Recent data
        "recent_documents": recent_documents,
        "recent_votes": recent_votes,
    }

    return render(request, "committee/committee_home.html", context)
