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
    is_scheduled = not legislation.is_available()

    # Author can always edit their own scheduled (not yet available) legislation
    # Officers can edit any legislation that hasn't passed
    if not (is_author or is_officer):
        return HttpResponseForbidden("You don't have permission to edit this legislation.")

    if is_author and not is_officer and not is_scheduled:
        return HttpResponseForbidden("You can only edit scheduled legislation before it becomes available.")

    # Check if the legislation has passed. If it has, redirect to submit a new version.
    if legislation.status == 'passed':
        messages.error(request, "This legislation has passed and cannot be edited. Please submit a new version.")
        return redirect('submit_new_version', legislation_id=legislation.id)

    # Check if voting has already started (cannot edit after voting begins)
    if legislation.voting_has_started() and not request.user.is_admin:
        messages.error(request, "This legislation cannot be edited because voting has already started.")
        return redirect('vote')

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
            form.save()
            messages.success(request, "Legislation has been updated.")
            return redirect('vote')  # Redirect back to the vote page
        else:
            messages.error(request, "Please correct the error below.")

    return render(request, 'edit_legislation.html', {'form': LegislationForm(instance=legislation), 'legislation': legislation})