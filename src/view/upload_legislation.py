import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect

from ..decorators import officer_required, log_function_call
from ..forms import LegislationForm
from src.feature_flag_decorators import require_feature_flag, check_feature_enabled
from src import chapter_votes
from src.models import Role, ParliamentUser
from src.utils.file_validation import validate_uploaded_file

logger = logging.getLogger(__name__)


@officer_required
@require_feature_flag('legislation_voting')
@log_function_call
def upload_legislation(request):

    if request.method == 'POST':

        # --- Chair Appointment ---
        if request.POST.get('action_type') == 'create_appointment':
            return _create_appointment(request)

        # --- General Legislation ---
        if 'document' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['document'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return redirect('upload_legislation')

        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            legislation = form.save(commit=False)
            legislation.posted_by = request.user
            # ⚠️ Enforced here, not just hidden in the template — a flag that
            # only hides a checkbox is not a flag, it's a suggestion. Forced
            # server-side regardless of what the submitted form contained.
            if not check_feature_enabled('anonymous_voting'):
                legislation.anonymous_vote = False
            if not check_feature_enabled('abstain_voting'):
                legislation.allow_abstain = False
            legislation.save()

            _notify(legislation)
            return redirect('vote')
        else:
            messages.error(request, 'There was an error with your submission.')
    else:
        form = LegislationForm()

    appointment_roles = Role.objects.all().order_by('name')
    appointment_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'vote.html', {
        'form': form,
        'profile': request.user,
        'appointment_roles': appointment_roles,
        'appointment_members': appointment_members,
        'legislation': [],
        'vote_data': {},
        'can_vote': False,
    })


def _create_appointment(request):
    """Handle chair appointment legislation creation (v4.4.2: src/chapter_votes.py)."""
    try:
        _leg, message, warning = chapter_votes.create_appointment(request.user, request.POST, request)
    except chapter_votes.ChapterVoteError as exc:
        messages.error(request, exc.message)
        return redirect('vote')
    if warning:
        messages.warning(request, warning)
    messages.success(request, message)
    return redirect('vote')


def _notify(legislation):
    """The availability announcement: ``chapter_votes.announce_if_available``."""
    chapter_votes.announce_if_available(legislation)
