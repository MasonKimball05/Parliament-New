from ..models import Legislation
from ..decorators import officer_required, log_function_call
from ..forms import LegislationForm
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file

@officer_required
@log_function_call
def submit_new_version(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can submit a new version.")

    if request.method == 'POST':
        # Validate uploaded file before processing form
        if 'document' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['document'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'submit_new_version.html', {'form': LegislationForm(instance=legislation), 'legislation': legislation})

        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            new_legislation = form.save(commit=False)
            new_legislation.posted_by = request.user
            # Optionally: mark this as a new version of the old legislation
            new_legislation.previous_version = legislation  # Assuming you have a previous_version field
            new_legislation.save()

            messages.success(request, "New version of the legislation has been submitted.")
            return redirect('view_legislation_history')  # Redirect to the history page or wherever appropriate
        else:
            messages.error(request, "Please correct the error below.")
    else:
        # Prepopulate the form with the old legislation data
        form = LegislationForm(instance=legislation)

    return render(request, 'submit_new_version.html', {'form': form, 'legislation': legislation})