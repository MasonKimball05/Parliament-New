from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.next_url import safe_referer
from src.committee_management import apply_vote_result, vote_tally
from src.models import CommitteeLegislation
from src.feature_flag_decorators import require_feature_flag
import logging

__all__ = ['recalculate_committee_vote']

logger = logging.getLogger('function_calls')


def get_vote_tally(legislation):
    """Helper to get vote tally for a piece of legislation"""
    return vote_tally(legislation)


@login_required
@require_feature_flag('committee_voting')
@require_POST
def recalculate_committee_vote(request, legislation_id):
    """Recalculate the pass/fail status of a committee vote"""
    legislation = get_object_or_404(CommitteeLegislation, id=legislation_id)
    committee = legislation.committee
    user = request.user

    # Check permissions - must be chair or officer
    is_chair = committee.is_chair(user)
    is_officer = user.member_type == 'Officer'

    if not (is_chair or is_officer):
        messages.error(request, "You don't have permission to recalculate this vote.")
        return redirect(safe_referer(request) or 'chapter_documents')

    if not legislation.voting_closed:
        messages.error(request, "Cannot recalculate - voting is still open.")
        return redirect(safe_referer(request) or 'chapter_documents')

    tally = get_vote_tally(legislation)
    total_votes = tally['total']

    if total_votes > 0:
        apply_vote_result(legislation, tally)
        legislation.save()
        result_text = "passed" if legislation.passed else "did not pass"
        logger.info(f"{user.username} recalculated vote result for '{legislation.title}' (ID: {legislation.id}) - {result_text}")
        messages.success(request, f"Vote result recalculated. The vote {result_text}.")
    else:
        messages.warning(request, "No votes to calculate result from.")

    # Redirect back to referring page (validate to prevent open redirect)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect('chapter_documents')
