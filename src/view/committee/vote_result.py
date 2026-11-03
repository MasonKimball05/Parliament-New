from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from src import committee_management as cm
from src.feature_flag_decorators import require_feature_flag
from src.models import Committee, CommitteeLegislation

__all__ = ['committee_vote_result']


@login_required
@require_feature_flag('committee_voting')
def committee_vote_result(request, code, legislation_id):
    """View detailed vote results for a committee legislation item"""
    committee = get_object_or_404(Committee, code=code)
    legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)
    user = request.user

    # v4.4.3: who may look, and the figures, are src/committee_management
    # (shared with the app's result screen).
    if not cm.can_view_vote_result(user, committee, legislation):
        messages.error(request, "You don't have permission to view these results.")
        return redirect('vote', code=code)

    context = {
        'committee': committee,
        'legislation': legislation,
        'anonymous': legislation.anonymous_vote,
        'is_chair': committee.is_chair(user),
        **cm.vote_result(legislation, user),
    }
    if legislation.vote_mode == 'plurality':
        context.update({
            'plurality_votes_allowed': legislation.plurality_votes_allowed,
            'runoff_enabled': legislation.plurality_runoff_enabled,
            'is_runoff': legislation.plurality_is_runoff,
            'parent_vote': legislation.plurality_parent,
        })
    return render(request, 'committee/vote_result.html', context)
