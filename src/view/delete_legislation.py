"""
View for admins to delete erroneous legislation from the vote page
Authors can also delete their own scheduled legislation before it becomes available.
"""
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Legislation, CommitteeLegislation
import logging

logger = logging.getLogger('function_calls')


@login_required
def delete_chapter_legislation(request, legislation_id):
    """Allow admins to delete legislation, or authors to delete their scheduled legislation"""
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Check permissions:
    # - Admins can always delete
    # - Authors can delete their own scheduled (not yet available) legislation
    is_admin = request.user.is_admin
    is_author = request.user == legislation.posted_by
    is_scheduled = not legislation.is_available()

    if not is_admin and not (is_author and is_scheduled):
        messages.error(request, "You don't have permission to delete this legislation.")
        return redirect('vote')

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
