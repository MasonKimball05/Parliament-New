from django.shortcuts import redirect, get_object_or_404
from ..decorators import officer_required, log_function_call
from ..models import Legislation
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from src.feature_flag_decorators import require_feature_flag

@officer_required
@require_feature_flag('legislation_voting')
@require_POST
@log_function_call
def reopen_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Prevent reopening if already passed
    if legislation.status == 'passed':
        messages.error(request, "This legislation has already passed and cannot be reopened.")
        return redirect('view_legislation_history')  # Redirect to the history page after the message

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can reopen this legislation.")

    # v3.13.3: also reset status — end_vote leaves it 'failed'/'passed', and
    # the vote page excludes those, so a reopened vote never reappeared on the
    # vote page. 'draft' is the open status. voting_ended_at is cleared so the
    # tally poller doesn't treat it as just-closed.
    legislation.voting_closed = False  # Reopen the voting
    legislation.status = 'draft'
    legislation.voting_ended_at = None
    legislation.save(update_fields=['voting_closed', 'status', 'voting_ended_at'])

    messages.success(request, "Legislation has been reopened.")
    return redirect('view_legislation_history')