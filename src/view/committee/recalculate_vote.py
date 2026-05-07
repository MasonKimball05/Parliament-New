from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.models import CommitteeLegislation, CommitteeVote
import logging

__all__ = ['recalculate_committee_vote']

logger = logging.getLogger('function_calls')


def get_vote_tally(legislation):
    """Helper to get vote tally for a piece of legislation"""
    votes = CommitteeVote.objects.filter(legislation=legislation)
    if legislation.vote_mode == 'plurality':
        tally = {opt: votes.filter(vote_choice=opt).count() for opt in (legislation.plurality_options or [])}
        tally['total'] = votes.count()
    else:
        tally = {
            'yes': votes.filter(vote_choice='yes').count(),
            'no': votes.filter(vote_choice='no').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
            'total': votes.count()
        }
    return tally


@login_required
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
        return redirect(request.META.get('HTTP_REFERER', 'chapter_documents'))

    if not legislation.voting_closed:
        messages.error(request, "Cannot recalculate - voting is still open.")
        return redirect(request.META.get('HTTP_REFERER', 'chapter_documents'))

    tally = get_vote_tally(legislation)
    total_votes = tally['total']

    if total_votes > 0:
        if legislation.vote_mode == 'plurality':
            options = {k: v for k, v in tally.items() if k != 'total'}
            if options:
                max_votes = max(options.values())
                legislation.passed = max_votes > 0
                legislation.status = 'passed' if legislation.passed else 'draft'
        elif legislation.vote_mode == 'piecewise':
            required = legislation.required_number or 0
            legislation.passed = tally.get('yes', 0) >= required
            legislation.status = 'passed' if legislation.passed else 'draft'
        else:
            yes_votes = tally.get('yes', 0)
            no_votes = tally.get('no', 0)
            countable_votes = yes_votes + no_votes
            if countable_votes > 0:
                yes_percentage = (yes_votes / countable_votes) * 100
                required_pct = int(legislation.required_percentage)
                legislation.passed = yes_percentage >= required_pct
                legislation.status = 'passed' if legislation.passed else 'draft'

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
