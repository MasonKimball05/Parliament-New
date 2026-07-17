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
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from src.utils.file_validation import validate_uploaded_file

@login_required
@log_function_call
def edit_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Check if user can edit this legislation
    is_author = request.user == legislation.posted_by or legislation.co_authors.filter(pk=request.user.pk).exists()
    is_officer = request.user.member_type in ['Chair', 'Officer']
    is_admin = request.user.is_admin
    is_scheduled = not legislation.is_available()

    # Tabled and pending legislation can be edited by author, officers, or admins
    is_editable_status = legislation.status in ['tabled', 'pending']

    # Author can always edit their own scheduled (not yet available) legislation
    # Officers/admins can edit any legislation that hasn't passed or failed
    if not (is_author or is_officer or is_admin):
        return HttpResponseForbidden("You don't have permission to edit this legislation.")

    # Check if the legislation has passed or failed - these cannot be edited
    if legislation.status == 'passed':
        messages.error(request, "This legislation has passed and cannot be edited. Please submit a new version.")
        return redirect('submit_new_version', legislation_id=legislation.id)

    if legislation.status == 'failed':
        messages.error(request, "This legislation has failed and cannot be edited. Please submit a new version.")
        return redirect('passed_legislation')

    # Check if voting has already started (cannot edit after voting begins)
    # UNLESS it's tabled or pending (these can always be edited)
    if not is_editable_status and legislation.voting_has_started() and not is_admin:
        messages.error(request, "This legislation cannot be edited because voting has already started.")
        return redirect('vote')

    # Authors can only edit scheduled legislation, unless it's tabled/pending
    if is_author and not is_officer and not is_admin and not is_scheduled and not is_editable_status:
        return HttpResponseForbidden("You can only edit scheduled legislation before it becomes available.")

    if request.method == 'POST':
        # Handle "mark as voted" — must be before status redirect checks
        if request.POST.get('action_type') == 'mark_voted':
            if not is_officer and not is_admin:
                return HttpResponseForbidden("Officers and admins only.")

            outcome = request.POST.get('mark_voted_outcome', 'passed')
            if outcome not in ('passed', 'failed'):
                outcome = 'passed'

            voted_at_str = request.POST.get('mark_voted_date', '').strip()
            voted_at = None
            if voted_at_str:
                try:
                    voted_at = parse_datetime(voted_at_str)
                    # v3.13.3: naive datetimes are stored as UTC by the DB
                    # layer — interpret the entered time in the local timezone
                    if voted_at and timezone.is_naive(voted_at):
                        voted_at = timezone.make_aware(voted_at)
                except Exception:
                    pass
            if not voted_at:
                voted_at = timezone.now()

            legislation.status = outcome
            legislation.passed = (outcome == 'passed')
            legislation.voting_closed = True
            legislation.voting_ended_at = voted_at

            try:
                yes_votes = int(request.POST.get('mark_voted_yes') or 0)
                no_votes = int(request.POST.get('mark_voted_no') or 0)
                abstain_votes = int(request.POST.get('mark_voted_abstain') or 0)
                if yes_votes or no_votes or abstain_votes:
                    legislation.historical_yes_votes = yes_votes
                    legislation.historical_no_votes = no_votes
                    legislation.historical_abstain_votes = abstain_votes
            except (ValueError, TypeError):
                pass

            legislation.save()
            logger.info(
                f"{request.user.username} marked legislation '{legislation.title}' (ID: {legislation.id}) "
                f"as {outcome} via mark_voted action"
            )
            messages.success(request, f'Legislation marked as {outcome}.')
            if outcome == 'passed' and legislation.legislation_type == 'appointment' and legislation.appointment_role:
                return redirect('assign_appointment', legislation_id=legislation.id)
            return redirect('passed_legislation')

        # Handle authorship transfer separately
        if request.POST.get('action_type') == 'transfer_authorship':
            # Only the primary author, officers, or admins can transfer
            if not (request.user == legislation.posted_by or is_officer or is_admin):
                return HttpResponseForbidden("You don't have permission to transfer authorship.")
            transfer_to_id = request.POST.get('transfer_to_id', '').strip()
            if not transfer_to_id:
                messages.error(request, "Please select a member to transfer authorship to.")
            else:
                new_author = ParliamentUser.objects.filter(user_id=transfer_to_id).first()
                if not new_author:
                    messages.error(request, "Member not found.")
                else:
                    old_author = legislation.posted_by
                    legislation.posted_by = new_author
                    # Remove new author from co_authors if they were one
                    legislation.co_authors.remove(new_author)
                    # Add old author as co-author
                    legislation.co_authors.add(old_author)
                    legislation.save(update_fields=['posted_by'])
                    messages.success(request, f"Authorship transferred to {new_author.name}. {old_author.name} has been added as a co-author.")
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
            saved_legislation = form.save(commit=False)

            # Handle status change for tabled/pending legislation
            if legislation.status in ['tabled', 'pending']:
                new_status = request.POST.get('status')
                if new_status in ['pending', 'active', 'tabled']:
                    old_status = saved_legislation.status
                    saved_legislation.status = new_status

                    # If changing to active, reopen voting
                    if new_status == 'active':
                        saved_legislation.voting_closed = False
                        saved_legislation.voting_ended_at = None
                        # Set voting_starts_at to now if not set
                        if not saved_legislation.voting_starts_at:
                            saved_legislation.voting_starts_at = timezone.now()
                        messages.success(request, f"Legislation has been updated and reintroduced to voting.")
                    elif new_status == 'tabled':
                        saved_legislation.voting_closed = True
                        saved_legislation.voting_ended_at = timezone.now()
                        messages.success(request, "Legislation has been updated and tabled.")
                    elif new_status == 'pending':
                        saved_legislation.voting_closed = False
                        saved_legislation.voting_ended_at = None
                        messages.success(request, "Legislation has been updated and set to pending.")
                    else:
                        messages.success(request, "Legislation has been updated.")
                else:
                    messages.success(request, "Legislation has been updated.")
            else:
                messages.success(request, "Legislation has been updated.")

            saved_legislation.save()

            # Handle co-authors update
            co_author_ids = request.POST.getlist('co_author_ids')
            co_author_users = ParliamentUser.objects.filter(user_id__in=co_author_ids)
            saved_legislation.co_authors.set(co_author_users)

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