from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from src.models import *

@login_required
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

    # Get all committees for dropdown and admin view
    all_committees_list = Committee.objects.select_related('role').all().order_by('name')

    # Prepare all committees info for dropdown
    all_committees_info = []
    for committee in all_committees_list:
        committee_vp = committee.get_vp()
        all_committees_info.append({
            'committee': committee,
            'vp': committee_vp,
        })

    # Determine which committees to display in main section
    if show_all:
        display_committees = all_committees_list
    else:
        display_committees = user_committees

    # Add role information to each committee
    committees_with_roles = []
    for committee in display_committees:
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

        # Get VP for this committee
        committee_vp = committee.get_vp()

        committees_with_roles.append({
            'committee': committee,
            'roles': ', '.join(roles) if roles else 'Not a member',
            'is_voting_member': is_voting_member,
            'committee_vp': committee_vp,
        })

    context = {
        'committees': committees_with_roles,
        'all_committees_info': all_committees_info,
        'show_all': show_all,
    }

    return render(request, 'committee/committee_index.html', context)
