import logging
from ..decorators import log_function_call
from ..forms import LegislationForm
from ..models import Legislation, ParliamentUser
from django.shortcuts import render, get_object_or_404, redirect

logger = logging.getLogger(__name__)
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file
from src import chapter_votes
from src.feature_flag_decorators import require_feature_flag

@login_required
@require_feature_flag('legislation_voting')
@log_function_call
def edit_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # v4.4.2: the gate, in the website's order, is src/chapter_votes.py
    # (shared with the app); each refusal answers as it always did.
    try:
        is_author, is_officer, is_admin = chapter_votes.edit_access(request.user, legislation)
    except chapter_votes.ChapterVoteError as exc:
        if exc.code == 'forbidden':
            return HttpResponseForbidden(exc.message)
        messages.error(request, exc.message)
        if exc.code == 'passed':
            return redirect('submit_new_version', legislation_id=legislation.id)
        if exc.code == 'failed':
            return redirect('passed_legislation')
        return redirect('vote')

    if request.method == 'POST':
        # Handle "mark as voted" — must be before status redirect checks
        if request.POST.get('action_type') == 'mark_voted':
            if not is_officer and not is_admin:
                return HttpResponseForbidden("Officers and admins only.")

            outcome = chapter_votes.mark_voted(legislation, request.user, request.POST)
            messages.success(request, f'Legislation marked as {outcome}.')
            if outcome == 'passed' and legislation.legislation_type == 'appointment' and legislation.appointment_role:
                return redirect('assign_appointment', legislation_id=legislation.id)
            return redirect('passed_legislation')

        # Handle authorship transfer separately
        if request.POST.get('action_type') == 'transfer_authorship':
            # Only the primary author, officers, or admins can transfer
            if not (request.user == legislation.posted_by or is_officer or is_admin):
                return HttpResponseForbidden("You don't have permission to transfer authorship.")
            try:
                messages.success(request, chapter_votes.transfer_authorship(
                    legislation, request.POST.get('transfer_to_id', '')))
            except chapter_votes.ChapterVoteError as exc:
                messages.error(request, exc.message)
            return redirect('edit_legislation', legislation_id=legislation.id)

        # Validate uploaded file before processing form
        if 'document' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['document'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'edit_legislation.html', {'form': LegislationForm(instance=legislation), 'legislation': legislation})

        form = LegislationForm(request.POST, request.FILES, instance=legislation)
        if form.is_valid():
            saved_legislation, message = chapter_votes.save_edit(
                form, legislation, request.POST.get('status'), request.POST.getlist('co_author_ids'))
            messages.success(request, message)

            # Redirect based on new status
            if saved_legislation.status in ['tabled', 'pending']:
                return redirect('passed_legislation')
            return redirect('vote')  # Redirect back to the vote page
        else:
            messages.error(request, "Please correct the error below.")

    all_members = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive']
    ).exclude(pk=legislation.posted_by.pk).order_by('name')
    current_co_authors = legislation.co_authors.values_list('user_id', flat=True)

    return render(request, 'edit_legislation.html', {
        'form': LegislationForm(instance=legislation),
        'legislation': legislation,
        'all_members': all_members,
        'current_co_authors': list(current_co_authors),
    })