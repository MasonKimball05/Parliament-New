"""
Public member directory view.
Shows basic member information visible to all authenticated members.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from src.models import ParliamentUser


@login_required
def member_directory(request):
    """Display a public directory of all active members."""

    # Get all active members, ordered by name
    members = ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        member_type='Advisor'  # Optionally exclude advisors from main list
    ).order_by('name')

    # Get advisors separately
    advisors = ParliamentUser.objects.filter(
        member_status='Active',
        member_type='Advisor'
    ).order_by('name')

    # Group members by type for display
    officers = [m for m in members if m.member_type == 'Officer']
    chairs = [m for m in members if m.member_type == 'Chair']
    regular_members = [m for m in members if m.member_type == 'Member']
    pledges = [m for m in members if m.member_type == 'Pledge']

    context = {
        'officers': officers,
        'chairs': chairs,
        'members': regular_members,
        'pledges': pledges,
        'advisors': advisors,
        'total_count': members.count() + advisors.count(),
    }

    return render(request, 'directory.html', context)
