from django.shortcuts import redirect, get_object_or_404
from ..decorators import officer_required, log_function_call
from ..models import Legislation
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from src import chapter_votes
from src.feature_flag_decorators import require_feature_flag

@officer_required
@require_feature_flag('legislation_voting')
@require_POST
@log_function_call
def reopen_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # v4.4.2: the rule (never a passed bill; only the uploader) and the reset
    # are src/chapter_votes.py, shared with the app.
    try:
        chapter_votes.reopen(legislation, request.user)
    except chapter_votes.ChapterVoteError as exc:
        if exc.code == 'forbidden':
            return HttpResponseForbidden(exc.message)
        messages.error(request, exc.message)
        return redirect('view_legislation_history')  # Redirect to the history page after the message

    messages.success(request, "Legislation has been reopened.")
    return redirect('view_legislation_history')
