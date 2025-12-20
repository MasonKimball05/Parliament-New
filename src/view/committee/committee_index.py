from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from src.models import *

@login_required
def committee_index(request):
    """Display all committees the user is associated with"""
    user = request.user

    # Get all committees where user is a member, chair, or advisor
    member_committees = user.committees.all()
    chair_committees = user.chair_roles.all()
    advisor_committees = user.advisor_roles.all()
    voting_committees = user.committee_voters.all()

    # Combine and remove duplicates
    all_committees = (member_committees | chair_committees | advisor_committees).distinct()

    # Add role information to each committee
    committees_with_roles = []
    for committee in all_committees:
        roles = []

        # Check each role individually by ID
        if chair_committees.filter(id=committee.id).exists():
            roles.append('Chair')
        if advisor_committees.filter(id=committee.id).exists():
            roles.append('Advisor')
        if member_committees.filter(id=committee.id).exists():
            roles.append('Member')

        # Check if voting member
        is_voting_member = voting_committees.filter(id=committee.id).exists()

        committees_with_roles.append({
            'committee': committee,
            'roles': ', '.join(roles),
            'is_voting_member': is_voting_member
        })

    context = {
        'committees': committees_with_roles,
    }

    return render(request, 'committee/committee_index.html', context)