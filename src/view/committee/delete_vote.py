"""
View for chairs/admins to delete committee votes
"""
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeLegislation, CommitteeVote
import logging

__all__ = ['delete_committee_vote']

logger = logging.getLogger('function_calls')


@login_required
def delete_committee_vote(request, code, legislation_id):
    """Allow chairs or admins to delete a committee vote"""
    committee = get_object_or_404(Committee, code=code)
    legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

    # Check permissions - must be chair or admin
    is_chair = committee.is_chair(request.user)
    is_admin = request.user.is_admin

    if not is_chair and not is_admin:
        messages.error(request, "Only committee chairs or admins can delete votes.")
        return redirect(f'/committee/{code}/vote/')

    if request.method == 'POST':
        title = legislation.title

        # Delete associated chapter legislation if it exists
        if legislation.chapter_legislation:
            chapter_leg = legislation.chapter_legislation
            legislation.chapter_legislation = None
            legislation.save()
            chapter_leg.delete()
            logger.info(f"Also deleted linked chapter legislation for '{title}'")

        # Delete all votes for this legislation
        vote_count = CommitteeVote.objects.filter(legislation=legislation).count()
        CommitteeVote.objects.filter(legislation=legislation).delete()
        logger.info(f"Deleted {vote_count} votes for '{title}'")

        # Delete the legislation itself
        legislation.delete()

        logger.info(f"{request.user.username} deleted committee vote '{title}' from {committee.code}")
        messages.success(request, f"Vote '{title}' has been permanently deleted.")

    return redirect(f'/committee/{code}/vote/')
