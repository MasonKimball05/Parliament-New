"""
View for chairs/admins to delete committee votes
"""
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.committee_management import delete_committee_vote as delete_committee_vote_and_ballots
from src.models import Committee, CommitteeLegislation
from src.feature_flag_decorators import require_feature_flag

__all__ = ['delete_committee_vote']


@login_required
@require_feature_flag('committee_voting')
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
        title = delete_committee_vote_and_ballots(legislation, request.user)
        messages.success(request, f"Vote '{title}' has been permanently deleted.")

    return redirect(f'/committee/{code}/vote/')
