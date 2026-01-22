"""
View for admins to delete erroneous legislation from the vote page
"""
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Legislation, CommitteeLegislation
import logging

logger = logging.getLogger('function_calls')


@login_required
def delete_chapter_legislation(request, legislation_id):
    """Allow admins to delete legislation that shouldn't exist (from vote page)"""
    if not request.user.is_admin:
        messages.error(request, "Only admins can delete legislation.")
        return redirect('vote')

    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.method == 'POST':
        title = legislation.title

        # Check if this was pushed from a committee and unlink it
        committee_leg = CommitteeLegislation.objects.filter(chapter_legislation=legislation).first()
        if committee_leg:
            committee_leg.chapter_legislation = None
            committee_leg.pushed_to_chapter = False
            committee_leg.save()
            logger.info(f"{request.user.username} unlinked chapter legislation '{title}' from committee legislation")

        # Delete the legislation
        legislation.delete()

        logger.info(f"{request.user.username} deleted chapter legislation '{title}' (ID: {legislation_id})")
        messages.success(request, f"Legislation '{title}' has been deleted.")

    return redirect('vote')
