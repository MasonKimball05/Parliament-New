from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.db.models import Count, Q
from src.models import (
    Committee, CommitteePermissions, CommitteeDocument,
    CommitteeVote
)
from datetime import timedelta

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
    voting_members = committee.members.filter(can_vote=True).count()

    # Get document stats
    total_documents = CommitteeDocument.objects.filter(committee=committee).count()
    recent_documents = CommitteeDocument.objects.filter(
        committee=committee
    ).select_related('uploaded_by').order_by('-uploaded_at')[:5]

    # Get recent votes (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    active_votes = CommitteeVote.objects.filter(
        committee=committee,
        is_active=True
    ).count()
    recent_votes = CommitteeVote.objects.filter(
        committee=committee,
        created_at__gte=thirty_days_ago
    ).order_by('-created_at')[:5]

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
            "voting_members": voting_members,
            "total_documents": total_documents,
            "active_votes": active_votes,
        },
        # Recent data
        "recent_documents": recent_documents,
        "recent_votes": recent_votes,
    }

    return render(request, "committee/home.html", context)
