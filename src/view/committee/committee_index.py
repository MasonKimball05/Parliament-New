from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from src.models import *
from src.feature_flag_decorators import require_page_enabled

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

    # Get all committees for dropdown and admin view
    all_committees_query = Committee.objects.select_related('role').all().order_by('name')

    # Filter by visibility (unless show_all for admin)
    if show_all:
        all_committees_list = list(all_committees_query)
    else:
        all_committees_list = [c for c in all_committees_query if c.is_visible_to(user)]

    # Prepare all committees info for dropdown (filtered by visibility)
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
        # Filter user's committees by visibility as well
        display_committees = [c for c in user_committees if c.is_visible_to(user)]

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
        'is_test_server': settings.DEBUG,  # Test server runs with DEBUG=True
    }

    return render(request, 'committee/committee_index.html', context)
