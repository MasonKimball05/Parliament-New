from ..decorators import *
from ..forms import *
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.core.exceptions import ValidationError
from django.utils import timezone
from src.utils.file_validation import validate_uploaded_file

@login_required
@log_function_call
def edit_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Check if user can edit this legislation
    is_author = request.user == legislation.posted_by
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
                    else:
                        messages.success(request, "Legislation has been updated.")
                else:
                    messages.success(request, "Legislation has been updated.")
            else:
                messages.success(request, "Legislation has been updated.")

            saved_legislation.save()

            # Redirect based on new status
            if saved_legislation.status in ['tabled', 'pending']:
                return redirect('passed_legislation')
            return redirect('vote')  # Redirect back to the vote page
        else:
            messages.error(request, "Please correct the error below.")

    return render(request, 'edit_legislation.html', {'form': LegislationForm(instance=legislation), 'legislation': legislation})